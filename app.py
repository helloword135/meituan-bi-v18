import streamlit as st
import pandas as pd
from io import BytesIO

from business_dashboard_v18 import (
    get_supabase_client,
    verify_auth_code,
    load_history_from_cloud,
    upsert_daily_to_cloud,
    calculate_dashboard,
    get_history_status,
    delete_project_data,
)


# =========================
# 商业展示配置：这里可自行修改
# =========================
ADMIN_WECHAT = "付费后联系"
PRODUCT_NAME = "美团团购BI看板 V18.3 稳定正式版"
PAY_IMAGE_PATH = "assets/wechat_pay.png"

PRICE_MONTH = "月卡：399元 / 城市"
PRICE_QUARTER = "季卡：999元 / 城市"
PRICE_YEAR = "年卡：1999元 / 城市"
PRICE_CUSTOM = "多城市 / 定制版：单独报价"
PRICE_NOTICE = "付款后请备注城市/项目名称，并联系管理员获取授权码。授权码到期后自动失效，续费后可延长有效期。"


st.set_page_config(page_title=PRODUCT_NAME, layout="wide")

st.title(PRODUCT_NAME)
st.caption("授权码收费版｜云历史库｜每日数据自动累计｜授权到期后自动无法使用")

st.sidebar.header("授权验证")

auth_code = st.sidebar.text_input(
    "请输入授权码",
    type="password",
    placeholder=""
)

project_code_input = st.sidebar.text_input(
    "项目/城市编码",
    value="",
    placeholder="",
    help="城市编码必须和授权码绑定的编码一致。"
)
if st.sidebar.button("数据库连接测试"):
    try:
        client = get_supabase_client()
        resp = client.table("auth_codes").select("*").execute()
        rows = resp.data or []
        st.sidebar.success("数据库连接成功")
        st.sidebar.json({
            "SUPABASE_URL": st.secrets["SUPABASE_URL"],
            "读取到的授权码数量": len(rows),
            "auth_codes前20行": rows[:20],
        })
    except Exception as e:
        st.sidebar.error("数据库连接失败")
        st.sidebar.exception(e)
if st.sidebar.button("验证授权", type="primary"):
    try:
        client = get_supabase_client()
        auth_info = verify_auth_code(client, auth_code, project_code_input)

        st.session_state["auth_ok"] = True
        st.session_state["auth_info"] = auth_info
        st.session_state["project_code"] = auth_info["project_code"]

        st.sidebar.success(f"授权成功，到期时间：{auth_info['expire_date']}")
    except Exception as e:
        st.session_state["auth_ok"] = False
        st.sidebar.error(str(e))

auth_ok = st.session_state.get("auth_ok", False)
auth_info = st.session_state.get("auth_info", None)
project_code = st.session_state.get("project_code", project_code_input)

if not auth_ok:
    st.warning("请先在左侧输入授权码并验证，通过后才能使用工具。")

    st.subheader("收费标准")
    price_df = pd.DataFrame([
        {"套餐": "月卡", "价格": PRICE_MONTH, "说明": "适合短期试用或单月冲刺"},
        {"套餐": "季卡", "价格": PRICE_QUARTER, "说明": "适合稳定使用，性价比更高"},
        {"套餐": "年卡", "价格": PRICE_YEAR, "说明": "适合长期固定使用"},
        {"套餐": "多城市/定制版", "价格": PRICE_CUSTOM, "说明": "支持多城市、功能定制、专属配置"},
    ])
    st.table(price_df)
    st.info(PRICE_NOTICE)

    st.subheader("购买授权")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 微信收款码")
        try:
            st.image(PAY_IMAGE_PATH, width=320)
        except Exception:
            st.info("请把微信收款码图片命名为 wechat_pay.png，放到 assets 文件夹。")

    with col2:
        st.markdown("### 联系管理员")
        st.write("付款后请联系管理员获取授权码。")
        st.code(f"微信号：{ADMIN_WECHAT}", language="text")

        st.markdown("### 授权说明")
        st.write("授权码支持有效期控制，到期后系统会自动停止使用。")
        st.write("如需续费，请联系管理员延长授权有效期。")

    st.stop()


st.success(f"授权已通过｜项目编码：{project_code}｜到期时间：{auth_info['expire_date']}")

with st.sidebar:
    st.divider()
    st.header("云历史库管理")

    if st.button("查看云历史库状态"):
        try:
            client = get_supabase_client()
            status = get_history_status(client, project_code)
            st.write(status)
        except Exception as e:
            st.error("读取状态失败。")
            st.exception(e)

    danger = st.checkbox("我确认要清空当前项目历史库")
    if danger:
        if st.button("清空当前项目历史库", type="primary"):
            try:
                client = get_supabase_client()
                delete_project_data(client, project_code)
                st.success(f"已清空项目 {project_code} 的历史数据。")
            except Exception as e:
                st.error("清空失败。")
                st.exception(e)


st.subheader("第一步：上传当天导出文件")
daily_file = st.file_uploader("上传当天导出Excel文件", type=["xlsx", "xls"], key="daily_file")

st.subheader("第二步：上传BD门店明细")
bd_file = st.file_uploader("上传BD门店明细.xlsx", type=["xlsx", "xls"], key="bd_file")

col1, col2, col3 = st.columns(3)

with col1:
    save_to_cloud = st.button("保存当天数据到云历史库", type="primary")

with col2:
    generate_dashboard = st.button("生成经营看板")

with col3:
    download_history = st.button("下载当前云历史库")


if save_to_cloud:
    if daily_file is None:
        st.error("请先上传当天导出文件。")
    else:
        try:
            client = get_supabase_client()
            inserted_count = upsert_daily_to_cloud(client, project_code, daily_file)
            st.success(f"当天数据已保存/覆盖到云历史库，共处理 {inserted_count} 行。")
            status = get_history_status(client, project_code)
            st.write("当前云历史库状态：")
            st.write(status)
        except Exception as e:
            st.error("保存失败，请检查文件字段或 Supabase 配置。")
            st.exception(e)


if generate_dashboard:
    if bd_file is None:
        st.error("请先上传BD门店明细.xlsx。")
    else:
        try:
            client = get_supabase_client()
            history_df = load_history_from_cloud(client, project_code)

            if history_df.empty:
                st.error("当前项目云历史库为空，请先上传当天导出文件并保存。")
            else:
                bd_df = pd.read_excel(bd_file)
                result = calculate_dashboard(history_df, bd_df)

                st.success("经营看板生成完成。")
                st.subheader("经营看板预览")
                st.dataframe(result, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    result.to_excel(writer, index=False, sheet_name="经营看板")
                output.seek(0)

                st.download_button(
                    label="下载 business_dashboard.xlsx",
                    data=output.getvalue(),
                    file_name="business_dashboard.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error("生成失败，请检查BD门店明细或历史库字段。")
            st.exception(e)


if download_history:
    try:
        client = get_supabase_client()
        history_df = load_history_from_cloud(client, project_code)

        if history_df.empty:
            st.warning("当前项目云历史库为空。")
        else:
            st.subheader("当前云历史库预览")
            st.dataframe(history_df, use_container_width=True)

            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                history_df.to_excel(writer, index=False, sheet_name="history")
            output.seek(0)

            st.download_button(
                label="下载 history.xlsx",
                data=output.getvalue(),
                file_name="history.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    except Exception as e:
        st.error("下载历史库失败。")
        st.exception(e)
