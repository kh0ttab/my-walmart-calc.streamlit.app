import streamlit as st
import pandas as pd
import numpy as np
import io
import xlsxwriter
from calc_engine import UnitEconomicsEngine

# --- PAGE CONFIG ---
st.set_page_config(page_title="Walmart Unit Economics", layout="wide")

# --- CSS STYLING ---
st.markdown("""
<style>
    .metric-card {background-color: #f0f2f6; padding: 15px; border-radius: 10px; border-left: 5px solid #4CAF50;}
    .metric-value {font-size: 24px; font-weight: bold;}
    .metric-label {font-size: 14px; color: #555;}
    .warning-text {color: #ff4b4b; font-weight: bold;}
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if 'data_df' not in st.session_state:
    st.session_state['data_df'] = None
if 'mappings' not in st.session_state:
    st.session_state['mappings'] = {}
if 'results' not in st.session_state:
    st.session_state['results'] = None
if 'scenarios' not in st.session_state:
    st.session_state['scenarios'] = {}

# --- HELPER FUNCTIONS ---
def generate_excel(df, assumptions):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Detailed Results', index=False)
        
        # Summary Sheet
        summary_data = {
            'Metric': ['Total Units', 'Total CBM', 'Total Weight (kg)', 'Total Inv Value', 'Est. Total Profit'],
            'Value': [
                df['qty'].sum(), 
                df['total_line_cbm'].sum(), 
                df['total_line_weight_kg'].sum(),
                df['total_purchase_cost'].sum(),
                (df['net_profit'] * df['qty']).sum()
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
        
        # Assumptions Sheet
        assump_df = pd.DataFrame(list(assumptions.items()), columns=['Parameter', 'Value'])
        assump_df.to_excel(writer, sheet_name='Assumptions', index=False)
        
    return output.getvalue()

# --- SIDEBAR ---
st.sidebar.title("CN -> USA Calculator")
st.sidebar.markdown("Unit economics for Walmart/WFS.")
st.sidebar.info("Upload your product list to begin.")

# --- TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["1. Upload", "2. Map Columns", "3. Assumptions", "4. Results", "5. Export"])

# ================= TAB 1: UPLOAD =================
with tab1:
    st.header("Upload Product Data")
    uploaded_file = st.file_uploader("Upload CSV or XLSX", type=['csv', 'xlsx'])
    
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            st.session_state['data_df'] = df
            st.success(f"Loaded {len(df)} rows successfully.")
            st.dataframe(df.head())
        except Exception as e:
            st.error(f"Error loading file: {e}")

# ================= TAB 2: MAPPING =================
with tab2:
    st.header("Map Columns")
    if st.session_state['data_df'] is not None:
        cols = ['(Select Column)'] + list(st.session_state['data_df'].columns)
        
        st.markdown("**Required Fields**")
        col1, col2, col3 = st.columns(3)
        with col1:
            m_sku = st.selectbox("SKU", cols, index=0, key='m_sku')
            m_qty = st.selectbox("Quantity", cols, index=0, key='m_qty')
            m_cost = st.selectbox("Unit Cost (Factory)", cols, index=0, key='m_cost')
        with col2:
            m_len = st.selectbox("Length", cols, index=0, key='m_len')
            m_wid = st.selectbox("Width", cols, index=0, key='m_wid')
            m_hgt = st.selectbox("Height", cols, index=0, key='m_hgt')
        with col3:
            m_wgt = st.selectbox("Weight", cols, index=0, key='m_wgt')
            m_price = st.selectbox("Selling Price", cols, index=0, key='m_price')
            
        st.markdown("**Optional Fields**")
        m_duty = st.selectbox("Duty Rate % Column (Optional)", cols, index=0, key='m_duty')
        
        if st.button("Save Mappings"):
            # Clean none
            mapping = {
                'sku': m_sku if m_sku != '(Select Column)' else None,
                'qty': m_qty if m_qty != '(Select Column)' else None,
                'unit_cost': m_cost if m_cost != '(Select Column)' else None,
                'length': m_len if m_len != '(Select Column)' else None,
                'width': m_wid if m_wid != '(Select Column)' else None,
                'height': m_hgt if m_hgt != '(Select Column)' else None,
                'weight': m_wgt if m_wgt != '(Select Column)' else None,
                'selling_price': m_price if m_price != '(Select Column)' else None,
                'duty_rate': m_duty if m_duty != '(Select Column)' else None
            }
            
            # Validation
            missing = [k for k, v in mapping.items() if v is None and k != 'duty_rate']
            if missing:
                st.error(f"Missing required mappings: {', '.join(missing)}")
            else:
                st.session_state['mappings'] = mapping
                st.success("Mappings Saved!")

    else:
        st.info("Please upload a file first.")

# ================= TAB 3: ASSUMPTIONS =================
with tab3:
    st.header("Assumptions & Constants")
    
    with st.expander("1. Input Units & Currency", expanded=True):
        col1, col2, col3 = st.columns(3)
        uom_dim = col1.selectbox("Dimension Units", ['cm', 'in'])
        uom_weight = col2.selectbox("Weight Units", ['kg', 'lb'])
        fx_rate = col3.number_input("Currency Rate (Multiplier to USD)", value=1.0, help="If cost is RMB 7.1, enter 0.1408")

    with st.expander("2. Freight & Logistics", expanded=True):
        st.info("Calculate Total Freight for this batch manually or input a quote.")
        col1, col2 = st.columns(2)
        total_freight_quote = col1.number_input("Total Freight Quote ($)", value=5000.0, step=100.0)
        alloc_method = col2.selectbox("Allocation Method", ['cbm', 'weight', 'hybrid'])
        
        st.markdown("---")
        st.markdown("**Container Defaults (Reference Only)**")
        c1, c2, c3 = st.columns(3)
        c1.metric("20GP Cap", "33 CBM")
        c2.metric("40GP Cap", "67 CBM")
        c3.metric("40HQ Cap", "76 CBM")

    with st.expander("3. Customs & Duties"):
        col1, col2, col3 = st.columns(3)
        mpf_rate = col1.number_input("MPF Rate", value=0.003464, format="%.6f")
        mpf_min = col2.number_input("MPF Min ($)", value=31.0)
        mpf_max = col3.number_input("MPF Max ($)", value=614.0)
        hmf_rate = st.number_input("HMF Rate (Ocean Only)", value=0.00125, format="%.5f")
        broker_fee = st.number_input("Brokerage/Entry Fee ($)", value=150.0)

    with st.expander("4. Walmart & WFS Fees"):
        st.warning("Ensure these match current WFS rate cards.")
        col1, col2 = st.columns(2)
        def_referral = col1.number_input("Default Referral Fee (%)", value=15.0)
        wfs_storage = col2.number_input("WFS Storage ($/cuft/mo)", value=0.87)
        
        st.markdown("**Simple WFS Fulfillment Estimator**")
        c1, c2, c3 = st.columns(3)
        wfs_base = c1.number_input("Base Fulfillment Fee ($)", value=3.45)
        wfs_weight_allow = c2.number_input("Base Weight Allowance (lb)", value=1.0)
        wfs_excess = c3.number_input("Excess Weight Fee ($/lb)", value=0.40)
        
        dim_div = st.number_input("Dim Weight Divisor", value=139.0)
        
        st.markdown("**Marketing & Returns**")
        c1, c2 = st.columns(2)
        ads_pct = c1.number_input("Ads Spend (% of Sales)", value=5.0)
        ret_pct = c2.number_input("Returns Allowance (% of Sales)", value=3.0)

    # Collect all assumptions
    current_assumptions = {
        'uom_dim': uom_dim,
        'uom_weight': uom_weight,
        'fx_rate': fx_rate,
        'freight_total_spend': total_freight_quote,
        'allocation_method': alloc_method,
        'mpf_rate': mpf_rate,
        'mpf_min': mpf_min,
        'mpf_max': mpf_max,
        'hmf_rate': hmf_rate,
        'brokerage_fee': broker_fee,
        'default_referral_pct': def_referral,
        'wfs_storage_rate': wfs_storage,
        'wfs_base_fee': wfs_base,
        'wfs_base_weight_lb': wfs_weight_allow,
        'wfs_excess_per_lb': wfs_excess,
        'dim_divisor': dim_div,
        'ads_pct_sales': ads_pct,
        'returns_pct_sales': ret_pct
    }
    
    if st.button("Run Calculations"):
        if st.session_state['data_df'] is not None and st.session_state['mappings']:
            engine = UnitEconomicsEngine(st.session_state['data_df'], st.session_state['mappings'], current_assumptions)
            engine.run_conversions()
            engine.calculate_landed_cost()
            engine.calculate_walmart_economics()
            st.session_state['results'] = engine.get_results()
            st.session_state['last_assumptions'] = current_assumptions
            st.success("Calculations Complete! Go to Results tab.")
            st.rerun() # Refresh to show results
        else:
            st.error("Missing Data or Mappings")

# ================= TAB 4: RESULTS =================
with tab4:
    st.header("Results Analysis")
    
    if st.session_state['results'] is not None:
        df_res = st.session_state['results']
        
        # High Level Metrics
        m1, m2, m3, m4 = st.columns(4)
        total_profit = (df_res['net_profit'] * df_res['qty']).sum()
        avg_margin = df_res['net_margin_pct'].mean()
        total_cbm = df_res['total_line_cbm'].sum()
        total_invest = df_res['total_purchase_cost'].sum()
        
        m1.metric("Est. Total Profit", f"${total_profit:,.2f}")
        m2.metric("Avg Margin %", f"{avg_margin:.1f}%")
        m3.metric("Total Volume (CBM)", f"{total_cbm:.2f}")
        m4.metric("Inventory Cost", f"${total_invest:,.2f}")

        # Visualization
        st.subheader("Profitability Distribution")
        chart_data = df_res[['sku', 'net_margin_pct', 'roi_pct']].set_index('sku')
        st.bar_chart(chart_data)

        # Detailed Table
        st.subheader("Detailed SKU Data")
        
        # Formatting for display
        display_cols = [
            'sku', 'qty', 'unit_cost_usd', 'landed_cost_unit', 
            'selling_price', 'wfs_fulfillment_fee', 'referral_fee',
            'net_profit', 'net_margin_pct', 'roi_pct', 'breakeven_price'
        ]
        
        st.dataframe(
            df_res[display_cols].style.format({
                'unit_cost_usd': '${:.2f}',
                'landed_cost_unit': '${:.2f}',
                'selling_price': '${:.2f}',
                'wfs_fulfillment_fee': '${:.2f}',
                'referral_fee': '${:.2f}',
                'net_profit': '${:.2f}',
                'net_margin_pct': '{:.1f}%',
                'roi_pct': '{:.1f}%',
                'breakeven_price': '${:.2f}'
            }).background_gradient(subset=['net_margin_pct'], cmap="RdYlGn", vmin=0, vmax=30)
        )
        
        # Container Utility Check
        st.markdown("### Container Utilization Check")
        if total_cbm < 33:
            st.info(f"Fits in 20GP ({total_cbm:.1f} / 33 CBM). Utilization: {total_cbm/33*100:.1f}%")
        elif total_cbm < 67:
             st.info(f"Fits in 40GP ({total_cbm:.1f} / 67 CBM). Utilization: {total_cbm/67*100:.1f}%")
        elif total_cbm < 76:
             st.info(f"Fits in 40HQ ({total_cbm:.1f} / 76 CBM). Utilization: {total_cbm/76*100:.1f}%")
        else:
             st.warning(f"Overflows 40HQ! Total CBM: {total_cbm:.1f}. You need multiple containers.")

    else:
        st.info("Run calculations in the Assumptions tab first.")

# ================= TAB 5: EXPORT =================
with tab5:
    st.header("Export Data")
    if st.session_state['results'] is not None:
        
        # Generate Excel
        excel_data = generate_excel(st.session_state['results'], st.session_state.get('last_assumptions', {}))
        
        st.download_button(
            label="Download Full Analysis (.xlsx)",
            data=excel_data,
            file_name="walmart_unit_economics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # CSV Option
        csv_data = st.session_state['results'].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Results (.csv)",
            data=csv_data,
            file_name="results.csv",
            mime="text/csv"
        )
    else:
        st.info("No results to export.")