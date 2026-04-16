import xmlrpc.client
import streamlit as st
import pandas as pd

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
@st.cache_data(ttl=600) # Cacheamos los datos por 10 minutos
def get_odoo_data():
    try:
        # Autenticación
        common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(URL))
        uid = common.authenticate(DB, USERNAME, PASSWORD, {})
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(URL))
        
        # Buscamos facturas publicadas (Out Invoice)
        domain = [['move_type', '=', 'out_invoice'], ['state', '=', 'posted']]
        fields = ['name', 'partner_id', 'invoice_date', 'invoice_date_due' ,'amount_total', 'amount_residual', 'payment_state']
        
        facturas = models.execute_kw(DB, uid, PASSWORD, 'account.move', 'search_read', [domain], {'fields': fields})
        
        # CAMBIO AQUÍ: Ahora SOLO devolvemos las facturas (que son datos puros guardables en caché)
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
    
    for fac in facturas_data:
        # AQUI REPLICAMOS LA LÓGICA (Versión simplificada para API)
        # Ojo: Como no tenemos la función _get_spot, asumimos una tasa del 10% 
        # (Idealmente, deberías extraer el % de los impuestos de la factura vía API)
        monto_teorico_detraccion = fac['amount_total'] * 0.10 
        
        # Lógica de estados de pago
        if fac['payment_state'] in ('paid', 'in_payment') or fac['amount_residual'] <= 0:
            monto_pendiente = 0.0
        else:
            # Si hay un pago parcial, esta lógica asume que restamos la diferencia
            # (Hacer el cruce de diarios 'Banco de la Nación' vía API requiere queries más avanzadas)
            monto_pendiente = monto_teorico_detraccion
            # Aquí podrías agregar otra consulta XML-RPC para buscar los pagos exactos de esta factura
            
        # Solo agregamos a la tabla si hay detracción
        if monto_teorico_detraccion > 0:

            if monto_pendiente <= 0:
                estado_visual = '🟢 Pagado'
            else:
                estado_visual = '🔴 Pendiente'

            vencimiento = fac.get('invoice_date_due')
            fecha_vencimiento_segura = vencimiento if vencimiento else 'Sin fecha'

            datos_procesados.append({
                'Factura': fac['name'],
                'Cliente': fac['partner_id'][1] if fac['partner_id'] else 'N/A',
                'Fecha': fac['invoice_date'],
                'Vencimiento': fecha_vencimiento_segura,
                'Total Factura (S/)': fac['amount_total'],
                'Detracción Pendiente (S/)': round(monto_pendiente, 2),
                'Estado': fac['payment_state'],
                'Status Detracción': estado_visual
            })
            
    # Convertimos a Pandas DataFrame para que se vea bonito en Streamlit
    df = pd.DataFrame(datos_procesados)
    
    # Mostramos métricas rápidas
    total_deuda_spot = df['Detracción Pendiente (S/)'].sum()
    st.metric(label="Total Detracciones por Cobrar", value=f"S/ {total_deuda_spot:,.2f}")

    # 2. CREAMOS LA FUNCIÓN DE COLORES PARA LA TABLA
    def pintar_estado(val):
        if 'Pagado' in str(val):
            return 'background-color: #d4edda; color: #155724; font-weight: bold;' # Verde claro
        elif 'Pendiente' in str(val):
            return 'background-color: #f8d7da; color: #721c24; font-weight: bold;' # Rojo claro
        return ''
    
    # Aplicamos el estilo solo a nuestra nueva columna
    df_estilizado = df.style.map(pintar_estado, subset=['Status Detracción'])
    
    # Mostramos la tabla interactiva
    st.dataframe(df_estilizado, use_container_width=True)
    
    # Opción para descargar a Excel
    st.download_button(
        label="📥 Descargar Reporte en CSV",
        data=df.to_csv(index=False).encode('utf-8'),
        file_name='reporte_detracciones.csv',
        mime='text/csv',
    )