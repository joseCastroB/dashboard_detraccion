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
# ==========================================
# --- NUEVO: OCULTAR MENÚS Y BOTONES DE GITHUB ---
# ==========================================
ocultar_menu_estilo = """
    <style>
    /* Oculta el menú principal de hamburguesa */
    #MainMenu {visibility: hidden;}
    
    /* Oculta el encabezado completo (donde están los botones de GitHub y Deploy) */
    header {visibility: hidden;}
    
    /* Oculta el pie de página por defecto de Streamlit */
    footer {visibility: hidden;}
    
    /* Asegura que el botón de 'Deploy' desaparezca en la nube */
    .stDeployButton {display:none;}
    </style>
"""
st.markdown(ocultar_menu_estilo, unsafe_allow_html=True)
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
        fields_fac = ['name', 'partner_id', 'invoice_date', 'invoice_date_due', 'amount_untaxed', 'amount_total', 'amount_residual', 'payment_state', 'invoice_line_ids']
        facturas = models.execute_kw(DB, uid, PASSWORD, 'account.move', 'search_read', [domain], {'fields': fields_fac})
        
        primeras_lineas_ids = [fac['invoice_line_ids'][0] for fac in facturas if fac.get('invoice_line_ids')]
        
        # --- RASTREO DE PAGOS PARCIALES VÍA API (CORREGIDO PARA ODOO 17+) ---
        pagos_bn_por_factura = {}
        facturas_parciales = [fac['name'] for fac in facturas if fac['payment_state'] == 'partial']
        
        if facturas_parciales:
            # CAMBIO 1: Buscamos en el campo 'memo' en lugar de 'ref'
            domain_pagos = [['memo', 'in', facturas_parciales], ['state', '=', 'posted']]
            
            # CAMBIO 2: Le pedimos a Odoo que nos devuelva el 'memo'
            pagos = models.execute_kw(DB, uid, PASSWORD, 'account.payment', 'search_read', [domain_pagos], {'fields': ['memo', 'amount', 'journal_id']})
            
            for p in pagos:
                nombre_diario = p['journal_id'][1].lower() if p.get('journal_id') else ''
                if 'nación' in nombre_diario or 'nacion' in nombre_diario or 'detrac' in nombre_diario:
                    # CAMBIO 3: Usamos el 'memo' para cruzarlo con el nombre de la factura
                    fac_name = p['memo']
                    pagos_bn_por_factura[fac_name] = pagos_bn_por_factura.get(fac_name, 0.0) + p['amount']
        
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
                fac['pagado_bn_parcial'] = pagos_bn_por_factura.get(fac['name'], 0.0)
        
        return facturas
    except Exception as e:
        st.error(f"Error conectando a Odoo: {e}")
        return []

# ==========================================
# 3. LÓGICA DE CÁLCULO Y VISUALIZACIÓN
# ==========================================
with st.spinner('Conectando a Odoo y rastreando pagos...'):
    facturas_data = get_odoo_data()

if facturas_data:
    datos_procesados = []
    
    traductor_estados = {
        'not_paid': 'REGISTRADO',
        'in_payment': 'PAGADO PARCIALMENTE',
        'paid': 'PAGADO',
        'partial': 'PAGADO PARCIALMENTE',
        'reversed': 'REVERTIDO'
    }
    
    for fac in facturas_data:
        porcentaje_aplica = fac.get('porcentaje_dinamico', 0.0)
        
        if porcentaje_aplica > 0:
            
            # 1. Calculamos la deuda teórica original
            if fac['amount_total'] > 700:
                detraccion_original = fac['amount_total'] * porcentaje_aplica
                texto_porcentaje = f"{int(fac.get('porcentaje_mostrar', 0.0))}%"
            else:
                detraccion_original = 0.0
                texto_porcentaje = "-"
                
            # 2. Calculamos los saldos PENDIENTES reales
            pendiente_bn = detraccion_original
            if fac['payment_state'] == 'partial':
                pendiente_bn -= fac.get('pagado_bn_parcial', 0.0) # Restamos lo que ya pagaron al BN
            elif fac['payment_state'] in ('paid', 'in_payment', 'reversed'):
                pendiente_bn = 0.0
                
            if pendiente_bn < 0: pendiente_bn = 0.0
            
            # El pendiente del BCP es el Saldo Total de la factura menos lo que falta pagar al BN
            pendiente_bcp = fac['amount_residual'] - pendiente_bn
            if pendiente_bcp < 0: pendiente_bcp = 0.0

            vencimiento = fac.get('invoice_date_due')
            estado_traducido = traductor_estados.get(fac['payment_state'], str(fac['payment_state']).upper())

            # 3. Lógica del Status Visual
            if detraccion_original == 0:
                estado_visual = '🟢 No Aplica' 
            elif pendiente_bn == 0:
                # Si la detracción pendiente es 0, ya está cubierta (se pone en verde)
                estado_visual = '🟢 Pagado' 
            else:
                estado_visual = '🔴 Pendiente'

            # 4. Lógica de los guiones (Ocultar lo que ya se pagó)
            if fac['payment_state'] in ('paid', 'in_payment', 'reversed'):
                mostrar_bn = '-'
                mostrar_bcp = '-'
                mostrar_pendiente = '-'
            else:
                # Si falta pagar, mostramos el saldo, si el saldo es 0 mostramos guion
                mostrar_bn = round(pendiente_bn, 2) if pendiente_bn > 0 else '-'
                mostrar_bcp = round(pendiente_bcp, 2) if pendiente_bcp > 0 else '-'
                mostrar_pendiente = round(fac['amount_residual'], 2) if fac['amount_residual'] > 0 else '-'

            datos_procesados.append({
                'N FACTURA': fac['name'],
                'CLIENTE': fac['partner_id'][1] if fac['partner_id'] else 'N/A',
                'FECHA': fac['invoice_date'],
                'VENCIMIENTO': vencimiento if vencimiento else '-',
                'IMPORTE SIN IMPUESTO': round(fac.get('amount_untaxed', 0.0), 2),
                'TOTAL CON IMPUESTOS': round(fac['amount_total'], 2),
                'DETRACCION PAGO BN': mostrar_bn,
                'PORCENTAJE': texto_porcentaje, 
                'IMPORTE PAGO BCP': mostrar_bcp,
                'PENDIENTE DE PAGO': mostrar_pendiente,
                'ESTADO DE PAGO': estado_traducido,
                'STATUS DETRACCIÓN': estado_visual
            })
            
    df = pd.DataFrame(datos_procesados)
    
    if not df.empty:
        
        def pintar_estado(val):
            if 'Pagado' in str(val) or 'No Aplica' in str(val):
                return 'background-color: #d4edda; color: #155724; font-weight: bold;'
            elif 'Pendiente' in str(val):
                return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
            return ''

        # --- NUEVO: Función estricta para redondear a 2 decimales ---
        def formato_moneda(val):
            # Si es un número, lo fuerza a 2 decimales (ej. 1650.00). Si es un guion '-', lo deja igual.
            if isinstance(val, (int, float)):
                return f"{val:.2f}"
            return val

        # Definimos qué columnas llevan este formato
        columnas_dinero = ['IMPORTE SIN IMPUESTO', 'TOTAL CON IMPUESTOS', 'DETRACCION PAGO BN', 'IMPORTE PAGO BCP', 'PENDIENTE DE PAGO']

        # Aplicamos colores Y el nuevo formato a la tabla al mismo tiempo
        df_estilizado = (
            df.style
            .map(pintar_estado, subset=['STATUS DETRACCIÓN'])
            .format(formato_moneda, subset=columnas_dinero)
        )
        st.dataframe(df_estilizado, use_container_width=True)
        
        # ==========================================
        # 4. GENERACIÓN DEL EXCEL CON TOTALES Y DISEÑO
        # ==========================================
        df_export = df.drop(columns=['STATUS DETRACCIÓN']).copy()
        
        #Calculamos los totales seguros (ignorando los guiones)
        total_impuestos = pd.to_numeric(df_export['TOTAL CON IMPUESTOS'], errors='coerce').sum()
        total_bn = pd.to_numeric(df_export['DETRACCION PAGO BN'], errors='coerce').sum()
        total_bcp = pd.to_numeric(df_export['IMPORTE PAGO BCP'], errors='coerce').sum()
        total_pendiente = pd.to_numeric(df_export['PENDIENTE DE PAGO'], errors='coerce').sum()

        # Sumatorias a prueba de fallos (ignora los guiones)
        totales = {
            'N FACTURA': '', 'CLIENTE': 'TOTALES', 'FECHA': '', 'VENCIMIENTO': '',
            'IMPORTE SIN IMPUESTO': '',
            'TOTAL CON IMPUESTOS': total_impuestos,
            'DETRACCION PAGO BN': total_bn,
            'PORCENTAJE': '',
            'IMPORTE PAGO BCP': total_bcp,
            'PENDIENTE DE PAGO': total_pendiente,
            'ESTADO DE PAGO': ''
        }
        
        df_export = pd.concat([df_export, pd.DataFrame([totales])], ignore_index=True)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Reporte', startrow=1, header=False)
            
            workbook = writer.book
            worksheet = writer.sheets['Reporte']
            
            formato_moneda = workbook.add_format({'num_format': '"S/" #,##0.00', 'valign': 'vcenter'})
            formato_normal = workbook.add_format({'valign': 'vcenter'})
            formato_centro = workbook.add_format({'align': 'center', 'valign': 'vcenter'})
            
            formato_cabecera = workbook.add_format({
                'bold': True, 'font_color': 'white', 'bg_color': 'black',
                'align': 'center', 'valign': 'vcenter', 'text_wrap': True, 'border': 1
            })
            
            for col_num, value in enumerate(df_export.columns.values):
                worksheet.write(0, col_num, value, formato_cabecera)
            
            worksheet.set_column('A:A', 18, formato_normal) 
            worksheet.set_column('B:B', 45, formato_normal) 
            worksheet.set_column('C:D', 13, formato_centro) 
            worksheet.set_column('E:E', 18, formato_moneda) 
            worksheet.set_column('F:F', 20, formato_moneda) 
            worksheet.set_column('G:G', 20, formato_moneda) 
            worksheet.set_column('H:H', 12, formato_centro) 
            worksheet.set_column('I:I', 20, formato_moneda) 
            worksheet.set_column('J:J', 20, formato_moneda) 
            worksheet.set_column('K:K', 18, formato_centro) 
            
            worksheet.set_row(0, 30)
            
        excel_data = output.getvalue()
        
        # --- MAQUETACIÓN INFERIOR (BOTÓN + TABLA DE TOTALES) ---
        st.write("") # Un pequeño espacio en blanco
        
        # Dividimos la pantalla: una columna pequeña a la izquierda, una más grande a la derecha
        col1, col2 = st.columns([1.5, 3.5])
        
        with col1:
            st.download_button(
                label="📊 Descargar Reporte en Excel",
                data=excel_data,
                file_name='Reporte_Detracciones_Exacto.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            
        with col2:
            # Construimos la tabla idéntica a la del cliente usando HTML
            html_totales = f"""
            <table style="width:100%; border-collapse: collapse; text-align: center; font-family: sans-serif;">
                <tr style="background-color: #f0f2f6; font-size: 11px; color: #31333F; text-transform: uppercase;">
                    <th style="border: 1px solid #ddd; padding: 6px;">Total con Impuestos</th>
                    <th style="border: 1px solid #ddd; padding: 6px;">Total BN</th>
                    <th style="border: 1px solid #ddd; padding: 6px;">Total BCP</th>
                    <th style="border: 1px solid #ddd; padding: 6px;">Pendiente Total</th>
                </tr>
                <tr style="font-weight: bold; font-size: 14px; color: #111;">
                    <td style="border: 1px solid #ddd; padding: 10px;">S/ {total_impuestos:,.2f}</td>
                    <td style="border: 1px solid #ddd; padding: 10px;">S/ {total_bn:,.2f}</td>
                    <td style="border: 1px solid #ddd; padding: 10px;">S/ {total_bcp:,.2f}</td>
                    <td style="border: 1px solid #ddd; padding: 10px;">S/ {total_pendiente:,.2f}</td>
                </tr>
            </table>
            """
            st.markdown(html_totales, unsafe_allow_html=True)
            
    else:
        st.success("¡Todo al día! No hay facturas con detracciones pendientes.")