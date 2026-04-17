import xmlrpc.client
import streamlit as st
import pandas as pd
import io

# ==========================================
# 1. CONFIGURACIÓN DE CONEXIÓN A ODOO
# ==========================================
# CAMBIO AQUÍ: Nombres de variables en MAYÚSCULAS
URL = st.secrets["ODOO_URL"]
DB = st.secrets["ODOO_DB"]
USERNAME = st.secrets["ODOO_USER"]
PASSWORD = st.secrets["ODOO_PASSWORD"]

# Configuración de la página Streamlit
st.set_page_config(page_title="Dashboard de Detracciones", layout="wide")
st.title("📊 Control de Detracciones Pendientes")


# ==========================================
# 2. FUNCIÓN PARA CONECTAR Y EXTRAER DATOS
# ==========================================
@st.cache_data(ttl=600)
def get_odoo_data():
    try:
        common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(URL))
        uid = common.authenticate(DB, USERNAME, PASSWORD, {})
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(URL))
        
        domain = [['move_type', '=', 'out_invoice'], ['state', '=', 'posted']]
        
        # AGREGAMOS 'amount_untaxed' para el Importe sin impuestos
        fields_fac = ['name', 'partner_id', 'invoice_date', 'invoice_date_due', 'amount_untaxed', 'amount_total', 'amount_residual', 'payment_state', 'invoice_line_ids']
        facturas = models.execute_kw(DB, uid, PASSWORD, 'account.move', 'search_read', [domain], {'fields': fields_fac})
        
        primeras_lineas_ids = [fac['invoice_line_ids'][0] for fac in facturas if fac.get('invoice_line_ids')]
        
        if primeras_lineas_ids:
            lineas = models.execute_kw(DB, uid, PASSWORD, 'account.move.line', 'read', [primeras_lineas_ids], {'fields': ['product_id']})
            linea_producto_map = {line['id']: line['product_id'][0] for line in lineas if line.get('product_id')}
            productos_ids = list(set(linea_producto_map.values()))
            
            campo_porcentaje = 'l10n_pe_withhold_percentage' 
            
            productos = models.execute_kw(DB, uid, PASSWORD, 'product.product', 'read', [productos_ids], {'fields': [campo_porcentaje]})
            producto_porcentaje_map = {prod['id']: prod.get(campo_porcentaje, 0.0) for prod in productos}
            
            for fac in facturas:
                porcentaje = 0.0
                if fac.get('invoice_line_ids'):
                    primera_linea_id = fac['invoice_line_ids'][0]
                    producto_id = linea_producto_map.get(primera_linea_id)
                    if producto_id:
                        porcentaje = producto_porcentaje_map.get(producto_id, 0.0)
                
                fac['porcentaje_dinamico'] = porcentaje / 100.0 if porcentaje else 0.0
                fac['porcentaje_mostrar'] = porcentaje 
        
        return facturas
    except Exception as e:
        st.error(f"Error conectando a Odoo: {e}")
        return []

# ==========================================
# 3. LÓGICA DE CÁLCULO Y VISUALIZACIÓN
# ==========================================
with st.spinner('Conectando a Odoo y extrayendo facturas...'):
    facturas_data = get_odoo_data()

if facturas_data:
    datos_procesados = []
    
    # Diccionario para traducir los estados de Odoo al español del reporte
    traductor_estados = {
        'not_paid': 'REGISTRADO',
        'in_payment': 'PAGADO PARCIALMENTE',
        'paid': 'PAGADO',
        'partial': 'PAGADO PARCIALMENTE',
        'reversed': 'REVERTIDO'
    }
    
    for fac in facturas_data:
        monto_teorico_detraccion = fac['amount_total'] * fac.get('porcentaje_dinamico', 0.0)
        
        # Solo procesamos si realmente tiene detracción
        if monto_teorico_detraccion > 0:
            importe_bcp = fac['amount_total'] - monto_teorico_detraccion
            vencimiento = fac.get('invoice_date_due')
            estado_traducido = traductor_estados.get(fac['payment_state'], str(fac['payment_state']).upper())

            # --- LÓGICA DE LA COLUMNA VISUAL ---
            if fac['payment_state'] in ('paid', 'in_payment', 'reversed') or fac['amount_residual'] <= 0:
                estado_visual = '🟢 Pagado'
            else:
                estado_visual = '🔴 Pendiente'

            # Mapeamos EXACTAMENTE como la imagen del cliente
            datos_procesados.append({
                'N FACTURA': fac['name'],
                'CLIENTE': fac['partner_id'][1] if fac['partner_id'] else 'N/A',
                'FECHA': fac['invoice_date'],
                'VENCIMIENTO': vencimiento if vencimiento else '-',
                'IMPORTE SIN IMPUESTO': fac.get('amount_untaxed', 0.0),
                'TOTAL CON IMPUESTOS': fac['amount_total'],
                'DETRACCION PAGO BN': monto_teorico_detraccion,
                'PORCENTAJE': f"{int(fac.get('porcentaje_mostrar', 0.0))}%",
                'IMPORTE PAGO BCP': importe_bcp,
                'PENDIENTE DE PAGO': fac['amount_residual'],
                'ESTADO DE PAGO': estado_traducido,
                'STATUS DETRACCIÓN': estado_visual
            })
            
    df = pd.DataFrame(datos_procesados)
    
    if not df.empty:
        # Mostramos la tabla en Streamlit (con el nuevo formato)
        st.dataframe(df, use_container_width=True)
        
        # ==========================================
        # 4. GENERACIÓN DEL EXCEL CON TOTALES Y DISEÑO
        # ==========================================
        df_export = df.copy()
        
        # Creamos la fila de totales
        totales = {
            'N FACTURA': '', 'CLIENTE': 'TOTALES', 'FECHA': '', 'VENCIMIENTO': '',
            'IMPORTE SIN IMPUESTO': '',
            'TOTAL CON IMPUESTOS': df_export['TOTAL CON IMPUESTOS'].sum(),
            'DETRACCION PAGO BN': df_export['DETRACCION PAGO BN'].sum(),
            'PORCENTAJE': '',
            'IMPORTE PAGO BCP': df_export['IMPORTE PAGO BCP'].sum(),
            'PENDIENTE DE PAGO': df_export['PENDIENTE DE PAGO'].sum(),
            'ESTADO DE PAGO': ''
        }
        
        # Añadimos la fila al final
        df_export = pd.concat([df_export, pd.DataFrame([totales])], ignore_index=True)
        
        # Convertimos a formato Excel en memoria
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # Exportamos los datos sin el encabezado por defecto (lo pintaremos nosotros)
            df_export.to_excel(writer, index=False, sheet_name='Reporte', startrow=1, header=False)
            
            workbook = writer.book
            worksheet = writer.sheets['Reporte']
            
            # --- CREAMOS LOS FORMATOS VISUALES ---
            formato_moneda = workbook.add_format({'num_format': '"S/" #,##0.00', 'valign': 'vcenter'})
            formato_normal = workbook.add_format({'valign': 'vcenter'})
            formato_centro = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
            
            # Formato exacto para la cabecera (Negro con letras blancas y centrado)
            formato_cabecera = workbook.add_format({
                'bold': True,
                'font_color': 'white',
                'bg_color': 'black',
                'align': 'center',
                'valign': 'vcenter',
                'text_wrap': True,
                'border': 1
            })
            
            # --- PINTAMOS LA CABECERA ---
            for col_num, value in enumerate(df_export.columns.values):
                worksheet.write(0, col_num, value, formato_cabecera)
            
            # --- AJUSTAMOS LOS ANCHOS DE LAS COLUMNAS ---
            worksheet.set_column('A:A', 18, formato_normal) # N Factura
            worksheet.set_column('B:B', 45, formato_normal) # Cliente (Súper ancho)
            worksheet.set_column('C:D', 13, formato_centro) # Fechas
            worksheet.set_column('E:E', 18, formato_moneda) # Importe sin impuesto
            worksheet.set_column('F:F', 20, formato_moneda) # Total con impuestos
            worksheet.set_column('G:G', 20, formato_moneda) # Detracción Pago BN
            worksheet.set_column('H:H', 12, formato_centro) # Porcentaje
            worksheet.set_column('I:I', 20, formato_moneda) # Importe Pago BCP
            worksheet.set_column('J:J', 20, formato_moneda) # Pendiente de pago
            worksheet.set_column('K:K', 18, formato_centro) # Estado de pago
            
            # Hacemos la fila de la cabecera un poco más alta para que respire el texto
            worksheet.set_row(0, 30)
            
        excel_data = output.getvalue()
        
        st.download_button(
            label="📊 Descargar Reporte en Excel",
            data=excel_data,
            file_name='Reporte_Detracciones_Exacto.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    else:
        st.success("¡Todo al día! No hay facturas con detracciones pendientes.")