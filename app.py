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

from shen_member_cloud import render_shen_member_cloud_monitor


# =========================
# 商业展示配置：这里可自行修改
# =========================
ADMIN_WECHAT = "付费后联系"
PRODUCT_NAME = "美团团购BI看板 V18.9 汇总增强版"
PAY_IMAGE_PATH = "assets/wechat_pay.png"

PRICE_MONTH = "月卡：99元 / 城市"
PRICE_QUARTER = "季卡：199元 / 城市"
PRICE_YEAR = "年卡：399元 / 城市"
PRICE_CUSTOM = "多城市 / 定制版：单独报价"
PRICE_NOTICE = "付款后请备注城市/项目名称，并联系管理员获取授权码。授权码到期后自动失效，续费后可延长有效期。"


# =========================
# 无BD版经营看板
# =========================
def calculate_dashboard_no_bd(df):
    shop_col = "门店名称"
    level_col = "门店分层"
    gtv_col = "实付验证GTV"
    smart_col = "智能点餐实付验证GTV"
    new_flag_col = "是否本月新签门店"
    new_gtv_col = "新签门店实付验证GTV"
    active_col = "是否本月新签门店动销达标"
    shelf_col = "是否货架达标"
    decorate_col = "是否装修头图达标"

    need_cols = [
        shop_col,
        level_col,
        gtv_col,
        smart_col,
        new_flag_col,
        new_gtv_col,
        active_col,
        shelf_col,
        decorate_col,
        "快照日期",
    ]

    missing = [c for c in need_cols if c not in df.columns]
    if missing:
        raise Exception("历史库缺少字段：" + "、".join(missing))

    df = df.copy()
    df["快照日期"] = pd.to_datetime(df["快照日期"], errors="coerce")

    agg_dict = {
        level_col: "first",
        gtv_col: "max",
        smart_col: "max",
        new_flag_col: "max",
        new_gtv_col: "max",
        active_col: "max",
        shelf_col: "max",
        decorate_col: "max",
    }

    df = df.groupby(["快照日期", shop_col], as_index=False).agg(agg_dict)
    df = df.sort_values(by=[shop_col, "快照日期"])

    latest_date = df["快照日期"].max()
    latest_df = df[df["快照日期"] == latest_date].copy()

    latest_df["月度累计GTV"] = pd.to_numeric(latest_df[gtv_col], errors="coerce").fillna(0)
    latest_df["月度累计智能点餐"] = pd.to_numeric(latest_df[smart_col], errors="coerce").fillna(0)
    latest_df["月度累计新签"] = pd.to_numeric(latest_df[new_flag_col], errors="coerce").fillna(0)
    latest_df["新签门店实付验证GTV"] = pd.to_numeric(latest_df[new_gtv_col], errors="coerce").fillna(0)

    result = latest_df[[
        shop_col,
        level_col,
        "月度累计GTV",
        "月度累计智能点餐",
        "月度累计新签",
        "新签门店实付验证GTV",
        active_col,
        shelf_col,
        decorate_col,
    ]].copy()

    return result


def build_dashboard_summary(result):
    summary_cols = [
        "月度累计GTV",
        "月度累计智能点餐",
        "新签门店实付验证GTV",
        "月度累计新签",
        "是否本月新签门店动销达标",
        "是否货架达标",
        "是否装修头图达标",
    ]

    df = result.copy()

    for col in summary_cols:
        if col not in df.columns:
            df[col] = 0

    for col in [
        "月度累计GTV",
        "月度累计智能点餐",
        "新签门店实付验证GTV",
        "月度累计新签",
        "是否本月新签门店动销达标",
        "是否货架达标",
        "是否装修头图达标",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    total_shop_count = len(df)
    gtv_positive_count = int((df["月度累计GTV"] > 0).sum())
    smart_positive_count = int((df["月度累计智能点餐"] > 0).sum())
    new_shop_count = int(df["月度累计新签"].sum())
    active_count = int(df["是否本月新签门店动销达标"].sum())
    shelf_count = int(df["是否货架达标"].sum())
    decorate_count = int(df["是否装修头图达标"].sum())

    summary = pd.DataFrame([
        {
            "汇总口径": "合计",
            "门店总数": total_shop_count,
            "有GTV门店数": gtv_positive_count,
            "有智能点餐门店数": smart_positive_count,
            "月度累计GTV": round(df["月度累计GTV"].sum(), 2),
            "月度累计智能点餐": round(df["月度累计智能点餐"].sum(), 2),
            "新签门店实付验证GTV": round(df["新签门店实付验证GTV"].sum(), 2),
            "月度累计新签": new_shop_count,
            "新签门店动销达标数": active_count,
            "货架达标数": shelf_count,
            "装修头图达标数": decorate_count,
        }
    ])

    return summary


st.set_page_config(page_title=PRODUCT_NAME, layout="wide")

st.title(PRODUCT_NAME)
st.caption("授权码收费版｜云历史库｜每日数据自动累计｜神会员监控｜授权到期后自动无法使用")

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


tab_main, tab_shen = st.tabs([
    "经营看板",
    "神会员监控"
])


with tab_main:
    st.subheader("第一步：上传当天导出文件")
    daily_file = st.file_uploader("上传当天导出Excel文件", type=["xlsx", "xls"], key="daily_file")

    st.subheader("第二步：上传BD门店明细")
    st.info("BD门店明细不是必传。不上传时，系统会生成无BD版经营看板；上传后，会生成带BD维度的经营看板。")
    bd_file = st.file_uploader("上传BD门店明细.xlsx（可选）", type=["xlsx", "xls"], key="bd_file")

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
        try:
            client = get_supabase_client()
            history_df = load_history_from_cloud(client, project_code)

            if history_df.empty:
                st.error("当前项目云历史库为空，请先上传当天导出文件并保存。")
            else:
                if bd_file is not None:
                    bd_df = pd.read_excel(bd_file)
                    result = calculate_dashboard(history_df, bd_df)
                    dashboard_mode = "带BD维度经营看板"
                else:
                    result = calculate_dashboard_no_bd(history_df)
                    dashboard_mode = "无BD版经营看板"

                summary_df = build_dashboard_summary(result)

                st.success(f"{dashboard_mode}生成完成。")

                st.subheader("看板数据汇总")
                st.dataframe(summary_df, use_container_width=True)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("月度累计GTV", f"{summary_df.loc[0, '月度累计GTV']:,.2f}")
                c2.metric("月度累计智能点餐", f"{summary_df.loc[0, '月度累计智能点餐']:,.2f}")
                c3.metric("新签门店GTV", f"{summary_df.loc[0, '新签门店实付验证GTV']:,.2f}")
                c4.metric("月度累计新签", f"{int(summary_df.loc[0, '月度累计新签'])}")

                st.subheader("经营看板预览")
                st.dataframe(result, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    summary_df.to_excel(writer, index=False, sheet_name="看板数据汇总")
                    result.to_excel(writer, index=False, sheet_name="经营看板")
                output.seek(0)

                st.download_button(
                    label="下载 business_dashboard.xlsx",
                    data=output.getvalue(),
                    file_name="business_dashboard.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error("生成失败，请检查历史库字段或BD门店明细。")
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


with tab_shen:
    client = get_supabase_client()
    render_shen_member_cloud_monitor(client, project_code)
