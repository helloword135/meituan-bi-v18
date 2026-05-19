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

from shen_member_cloud import (
    render_shen_member_cloud_monitor,
    load_shen_member_from_cloud,
    calc_shen_city_report,
)


# =========================
# 商业展示配置：这里可自行修改
# =========================
ADMIN_WECHAT = "付费后联系"
PRODUCT_NAME = "美团团购BI看板 V19.2 KPI目标云保存版"
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



def get_latest_dashboard_summary(client, project_code):
    history_df = load_history_from_cloud(client, project_code)
    if history_df.empty:
        return None

    result = calculate_dashboard_no_bd(history_df)
    summary_df = build_dashboard_summary(result)
    return summary_df.iloc[0].to_dict()


def get_latest_shen_rate(client, project_code):
    try:
        shen_df = load_shen_member_from_cloud(client, project_code)
        if shen_df.empty:
            return 0.0

        city_report = calc_shen_city_report(shen_df)
        if city_report.empty:
            return 0.0

        latest_date = city_report["日期"].max()
        latest = city_report[city_report["日期"] == latest_date].iloc[-1]
        return float(latest["deal覆盖率"])
    except Exception:
        return 0.0


def _score_by_cap(rate, weight, cap=1.5):
    return round(min(rate, cap) * weight, 2)


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_main_history_summary(project_code):
    client = get_supabase_client()
    history_df = load_history_from_cloud(client, project_code)
    if history_df.empty:
        return None
    result = calculate_dashboard_no_bd(history_df)
    summary_df = build_dashboard_summary(result)
    return summary_df.iloc[0].to_dict()


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_shen_latest_rate(project_code):
    client = get_supabase_client()
    try:
        shen_df = load_shen_member_from_cloud(client, project_code)
        if shen_df.empty:
            return 0.0
        city_report = calc_shen_city_report(shen_df)
        if city_report.empty:
            return 0.0
        latest_date = city_report["日期"].max()
        latest = city_report[city_report["日期"] == latest_date].iloc[-1]
        return float(latest["deal覆盖率"])
    except Exception:
        return 0.0



def load_kpi_targets(client, project_code):
    default_targets = {
        "target_gtv": 3031488.0,
        "target_smart": 181889.0,
        "target_new_gtv": 136477.0,
        "target_shen_rate": 85.0,
    }

    try:
        resp = (
            client.table("kpi_targets")
            .select("*")
            .eq("project_code", project_code)
            .limit(1)
            .execute()
        )

        rows = resp.data or []

        if not rows:
            return default_targets

        row = rows[0]

        return {
            "target_gtv": float(row.get("target_gtv") or default_targets["target_gtv"]),
            "target_smart": float(row.get("target_smart") or default_targets["target_smart"]),
            "target_new_gtv": float(row.get("target_new_gtv") or default_targets["target_new_gtv"]),
            "target_shen_rate": float(row.get("target_shen_rate") or default_targets["target_shen_rate"]),
        }

    except Exception:
        return default_targets


def save_kpi_targets(client, project_code, target_gtv, target_smart, target_new_gtv, target_shen_rate):
    record = {
        "project_code": project_code,
        "target_gtv": float(target_gtv),
        "target_smart": float(target_smart),
        "target_new_gtv": float(target_new_gtv),
        "target_shen_rate": float(target_shen_rate),
    }

    client.table("kpi_targets").upsert(record, on_conflict="project_code").execute()


def render_kpi_monitor(client, project_code):
    st.subheader("KPI监控")
    st.info("目标值由城市自行输入；完成值自动从云历史库月累计数据带出。")

    summary = get_cached_main_history_summary(project_code)

    if summary is None:
        st.warning("当前项目主业务云历史库为空，暂无法自动带出KPI完成值。")
        return

    completed_gtv = float(summary.get("月度累计GTV", 0) or 0)
    completed_smart = float(summary.get("月度累计智能点餐", 0) or 0)
    completed_new_gtv = float(summary.get("新签门店实付验证GTV", 0) or 0)
    completed_shen_rate = float(get_cached_shen_latest_rate(project_code) or 0)

    st.markdown("### 目标配置")

    kpi_targets = load_kpi_targets(client, project_code)

    target_gtv = st.number_input(
        "实付验证GTV目标",
        min_value=0.0,
        value=float(kpi_targets["target_gtv"]),
        step=1000.0,
        key=f"target_gtv_{project_code}"
    )

    target_smart = st.number_input(
        "智能点餐GTV目标",
        min_value=0.0,
        value=float(kpi_targets["target_smart"]),
        step=1000.0,
        key=f"target_smart_{project_code}"
    )

    target_new_gtv = st.number_input(
        "新签门店GTV目标",
        min_value=0.0,
        value=float(kpi_targets["target_new_gtv"]),
        step=1000.0,
        key=f"target_new_gtv_{project_code}"
    )

    target_shen_rate = st.number_input(
        "神券报名率目标（%）",
        min_value=0.0,
        max_value=100.0,
        value=float(kpi_targets["target_shen_rate"]),
        step=1.0,
        key=f"target_shen_rate_{project_code}"
    )

    if st.button("保存当前城市KPI目标", type="primary", key=f"save_kpi_targets_{project_code}"):
        try:
            save_kpi_targets(
                client,
                project_code,
                target_gtv,
                target_smart,
                target_new_gtv,
                target_shen_rate
            )
            st.success(f"项目 {project_code} 的KPI目标已保存。下次登录会自动带出。")
        except Exception as e:
            st.error("KPI目标保存失败，请确认 Supabase 已执行 kpi_targets 建表SQL。")
            st.exception(e)

    rows = []

    rate_gtv = completed_gtv / target_gtv if target_gtv > 0 else 0
    score_gtv = _score_by_cap(rate_gtv, 75, 1.5)
    rows.append({
        "考核指标名称": "实付验证GTV完成率（去刷退）",
        "权重": "75%",
        "目标": round(target_gtv, 2),
        "完成": round(completed_gtv, 2),
        "完成率": f"{rate_gtv * 100:.2f}%",
        "得分": score_gtv,
    })

    rate_smart = completed_smart / target_smart if target_smart > 0 else 0
    score_smart = _score_by_cap(rate_smart, 15, 1.5)
    rows.append({
        "考核指标名称": "线下推广-智能点餐GTV完成率",
        "权重": "15%",
        "目标": round(target_smart, 2),
        "完成": round(completed_smart, 2),
        "完成率": f"{rate_smart * 100:.2f}%",
        "得分": score_smart,
    })

    rate_new_gtv = completed_new_gtv / target_new_gtv if target_new_gtv > 0 else 0
    score_new_gtv = _score_by_cap(rate_new_gtv, 10, 1.2)
    rows.append({
        "考核指标名称": "招货系统-新签门店GTV完成率",
        "权重": "10%",
        "目标": round(target_new_gtv, 2),
        "完成": round(completed_new_gtv, 2),
        "完成率": f"{rate_new_gtv * 100:.2f}%",
        "得分": score_new_gtv,
    })

    shen_rate = completed_shen_rate / target_shen_rate if target_shen_rate > 0 else 0
    shen_deduct = 0 if completed_shen_rate >= target_shen_rate else -5
    rows.append({
        "考核指标名称": "神券报名率",
        "权重": "扣分项",
        "目标": f"{target_shen_rate:.2f}%",
        "完成": f"{completed_shen_rate:.2f}%",
        "完成率": f"{shen_rate * 100:.2f}%",
        "得分": shen_deduct,
    })

    kpi_df = pd.DataFrame(rows)
    total_score = round(score_gtv + score_smart + score_new_gtv + shen_deduct, 2)

    st.markdown("### KPI得分")
    c1, c2, c3 = st.columns(3)
    c1.metric("总得分", total_score)
    c2.metric("GTV完成率", f"{rate_gtv * 100:.2f}%")
    c3.metric("神券报名率", f"{completed_shen_rate:.2f}%")

    st.dataframe(kpi_df, use_container_width=True)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        kpi_df.to_excel(writer, index=False, sheet_name="KPI监控")
    output.seek(0)

    st.download_button(
        label="下载KPI监控.xlsx",
        data=output.getvalue(),
        file_name="KPI监控.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

st.set_page_config(page_title=PRODUCT_NAME, layout="wide")

st.title(PRODUCT_NAME)
st.caption("授权码收费版｜云历史库｜每日数据自动累计｜神会员监控｜KPI监控｜目标云保存｜性能优化｜授权到期后自动无法使用")

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
                st.dataframe(result.head(500), use_container_width=True)
                st.caption(f"当前仅预览前500行，共 {len(result)} 行；完整数据请下载Excel。")

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
    left_col, right_col = st.columns([2, 1])

    with left_col:
        render_shen_member_cloud_monitor(get_supabase_client(), project_code)

    with right_col:
        render_kpi_monitor(get_supabase_client(), project_code)
