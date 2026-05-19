
import re
from io import BytesIO

import pandas as pd
import streamlit as st


SHEN_TABLE = "shen_member_history"
SHOP_MASTER_TABLE = "shop_master"

REQUIRED_SHEN_COLS = [
    "门店名称",
    "是否报名",
    "是否重点门店",
    "是否膨胀报名",
]

REQUIRED_BD_COLS = [
    "门店名称",
    "BD",
]


def _normalize_flag(series):
    return (
        series
        .fillna(0)
        .astype(str)
        .str.strip()
        .replace({
            "1": "1", "1.0": "1", "是": "1", "TRUE": "1", "True": "1", "true": "1",
            "已报名": "1", "已参与": "1", "Y": "1", "y": "1",
            "0": "0", "0.0": "0", "否": "0", "FALSE": "0", "False": "0", "false": "0",
            "未报名": "0", "未参与": "0", "N": "0", "n": "0",
        })
        .apply(lambda x: 1 if x == "1" else 0)
    )


def _date_from_filename(filename):
    name = filename or ""
    m = re.search(r"(20\d{2})[-_/\.]?(0?\d|1[0-2])[-_/\.]?([0-3]?\d)", name)
    if not m:
        return None
    y, mth, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    try:
        return pd.to_datetime(f"{y}-{mth}-{d}").strftime("%Y-%m-%d")
    except Exception:
        return None


def validate_shen_df(df):
    missing = [c for c in REQUIRED_SHEN_COLS if c not in df.columns]
    if missing:
        raise Exception("神会员数据缺少字段：" + "、".join(missing))


def validate_bd_df(df):
    missing = [c for c in REQUIRED_BD_COLS if c not in df.columns]
    if missing:
        raise Exception("BD门店明细缺少字段：" + "、".join(missing))


def prepare_shen_df(df, filename=None):
    validate_shen_df(df)
    df = df.copy()

    df["门店名称"] = df["门店名称"].astype(str).str.strip()

    if "快照日期" not in df.columns:
        business_date = _date_from_filename(filename)
        if business_date is None:
            raise Exception(
                "神会员数据缺少【快照日期】字段，且文件名无法识别日期。"
                "请把文件名改成 2026-05-18.xlsx 这种格式，或在表格中增加【快照日期】列。"
            )
        df["快照日期"] = business_date
    else:
        df["快照日期"] = pd.to_datetime(df["快照日期"], errors="coerce").dt.strftime("%Y-%m-%d")

    df = df[df["快照日期"].notna()]

    for col in ["是否报名", "是否重点门店", "是否膨胀报名"]:
        df[col] = _normalize_flag(df[col])

    if "来源文件" not in df.columns:
        df["来源文件"] = filename or ""

    return df


def prepare_bd_df(df):
    validate_bd_df(df)
    df = df.copy()

    df["门店名称"] = df["门店名称"].astype(str).str.strip()
    df["BD"] = df["BD"].astype(str).str.strip()

    df = df[df["门店名称"].notna()]
    df = df[df["门店名称"] != ""]
    df = df[df["BD"].notna()]
    df = df[df["BD"] != ""]
    df = df[df["BD"].str.lower() != "nan"]

    df = df.drop_duplicates(subset=["门店名称"], keep="last")

    return df


def _row_to_json(row, columns):
    data_json = {}
    for col in columns:
        val = row[col]
        if pd.isna(val):
            data_json[col] = None
        elif isinstance(val, pd.Timestamp):
            data_json[col] = val.strftime("%Y-%m-%d")
        else:
            data_json[col] = val.item() if hasattr(val, "item") else val
    return data_json


def _load_json_table(client, table_name, project_code):
    all_rows = []
    start = 0
    page_size = 1000

    while True:
        resp = (
            client.table(table_name)
            .select("data")
            .eq("project_code", project_code)
            .range(start, start + page_size - 1)
            .execute()
        )

        rows = resp.data or []
        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    if not all_rows:
        return pd.DataFrame()

    return pd.DataFrame([r["data"] for r in all_rows])


def upsert_shop_master_to_cloud(client, project_code, uploaded_file):
    df_raw = pd.read_excel(uploaded_file)
    df = prepare_bd_df(df_raw)

    client.table(SHOP_MASTER_TABLE).delete().eq("project_code", project_code).execute()

    records = []
    for _, row in df.iterrows():
        records.append({
            "project_code": project_code,
            "shop_name": str(row["门店名称"]),
            "bd": str(row["BD"]),
            "data": _row_to_json(row, df.columns),
        })

    for i in range(0, len(records), 500):
        client.table(SHOP_MASTER_TABLE).insert(records[i:i + 500]).execute()

    return len(records)


def load_shop_master_from_cloud(client, project_code):
    all_rows = []
    start = 0
    page_size = 1000

    while True:
        resp = (
            client.table(SHOP_MASTER_TABLE)
            .select("shop_name,bd,data")
            .eq("project_code", project_code)
            .range(start, start + page_size - 1)
            .execute()
        )

        rows = resp.data or []
        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    if not all_rows:
        return pd.DataFrame(columns=["门店名称", "BD"])

    # 优先用独立列，兼容data列
    result = []
    for r in all_rows:
        data = r.get("data") or {}
        shop_name = r.get("shop_name") or data.get("门店名称")
        bd = r.get("bd") or data.get("BD")
        result.append({"门店名称": shop_name, "BD": bd})

    df = pd.DataFrame(result)
    if df.empty:
        return pd.DataFrame(columns=["门店名称", "BD"])

    df["门店名称"] = df["门店名称"].astype(str).str.strip()
    df["BD"] = df["BD"].astype(str).str.strip()
    df = df[df["门店名称"].notna()]
    df = df[df["门店名称"] != ""]
    df = df[df["BD"].notna()]
    df = df[df["BD"] != ""]
    df = df[df["BD"].str.lower() != "nan"]
    df = df.drop_duplicates(subset=["门店名称"], keep="last")

    return df


def get_shop_master_status(client, project_code):
    df = load_shop_master_from_cloud(client, project_code)

    if df.empty:
        return {
            "项目编码": project_code,
            "BD档案库状态": "暂无BD档案"
        }

    return {
        "项目编码": project_code,
        "BD档案门店数": len(df),
        "BD人数": df["BD"].nunique(),
    }


def delete_shop_master(client, project_code):
    client.table(SHOP_MASTER_TABLE).delete().eq("project_code", project_code).execute()


def upsert_shen_member_to_cloud(client, project_code, uploaded_file):
    df_raw = pd.read_excel(uploaded_file)
    df = prepare_shen_df(df_raw, filename=getattr(uploaded_file, "name", ""))

    date_list = sorted(df["快照日期"].dropna().unique().tolist())

    for date_text in date_list:
        client.table(SHEN_TABLE).delete().eq("project_code", project_code).eq("snapshot_date", date_text).execute()

    records = []
    for _, row in df.iterrows():
        records.append({
            "project_code": project_code,
            "snapshot_date": row["快照日期"],
            "shop_name": str(row.get("门店名称", "")),
            "data": _row_to_json(row, df.columns),
        })

    for i in range(0, len(records), 500):
        client.table(SHEN_TABLE).insert(records[i:i + 500]).execute()

    return len(records), date_list


def load_shen_member_from_cloud(client, project_code):
    df = _load_json_table(client, SHEN_TABLE, project_code)

    if not df.empty and "快照日期" in df.columns:
        df["快照日期"] = pd.to_datetime(df["快照日期"], errors="coerce").dt.strftime("%Y-%m-%d")

    return df


def get_shen_member_status(client, project_code):
    df = load_shen_member_from_cloud(client, project_code)

    if df.empty:
        return {
            "项目编码": project_code,
            "神会员历史库状态": "暂无神会员数据"
        }

    return {
        "项目编码": project_code,
        "神会员记录数": len(df),
        "神会员门店数": df["门店名称"].nunique() if "门店名称" in df.columns else None,
        "最早日期": str(pd.to_datetime(df["快照日期"]).min().date()) if "快照日期" in df.columns else None,
        "最新日期": str(pd.to_datetime(df["快照日期"]).max().date()) if "快照日期" in df.columns else None,
    }



def get_shen_uploaded_dates(client, project_code):
    """
    查询当前项目已上传的神会员日期清单：
    日期、记录数、门店数。
    """
    all_rows = []
    start = 0
    page_size = 1000

    while True:
        resp = (
            client.table(SHEN_TABLE)
            .select("snapshot_date,shop_name")
            .eq("project_code", project_code)
            .range(start, start + page_size - 1)
            .execute()
        )

        rows = resp.data or []
        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    if not all_rows:
        return pd.DataFrame(columns=["日期", "记录数", "门店数"])

    df = pd.DataFrame(all_rows)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    result = (
        df.groupby("snapshot_date")
        .agg(
            记录数=("shop_name", "count"),
            门店数=("shop_name", "nunique")
        )
        .reset_index()
        .rename(columns={"snapshot_date": "日期"})
        .sort_values("日期")
    )

    return result


def delete_shen_member_data(client, project_code):
    client.table(SHEN_TABLE).delete().eq("project_code", project_code).execute()


def calc_shen_city_report(shen_df):
    df = prepare_shen_df(shen_df)
    result_list = []

    for business_date in sorted(df["快照日期"].drop_duplicates()):
        day_df = df[df["快照日期"] == business_date]

        deal_total = len(day_df)
        deal_signup = len(day_df[day_df["是否报名"] == 1])
        deal_rate = round(deal_signup / deal_total * 100, 2) if deal_total > 0 else 0

        expand_df = day_df[day_df["是否重点门店"] == 1]
        expand_total = len(expand_df)

        expand_signup = len(day_df[(day_df["是否重点门店"] == 1) & (day_df["是否膨胀报名"] == 1)])
        expand_rate = round(expand_signup / expand_total * 100, 2) if expand_total > 0 else 0

        result_list.append({
            "日期": business_date,
            "deal总数": deal_total,
            "deal报名数": deal_signup,
            "deal覆盖率": deal_rate,
            "膨胀deal总数": expand_total,
            "膨胀deal报名数": expand_signup,
            "膨胀deal覆盖率": expand_rate,
        })

    return pd.DataFrame(result_list)


def calc_shen_bd_report(shen_df, bd_mapping_df):
    shen_df = prepare_shen_df(shen_df)

    if bd_mapping_df.empty:
        return pd.DataFrame(), shen_df.copy()

    bd_df = bd_mapping_df.copy()
    bd_df["门店名称"] = bd_df["门店名称"].astype(str).str.strip()
    bd_df["BD"] = bd_df["BD"].astype(str).str.strip()
    bd_df = bd_df.drop_duplicates(subset=["门店名称"], keep="last")

    merge_df = shen_df.merge(bd_df[["门店名称", "BD"]], on="门店名称", how="left")

    unmatched_df = merge_df[
        merge_df["BD"].isna()
        |
        (merge_df["BD"].astype(str).str.strip() == "")
        |
        (merge_df["BD"].astype(str).str.lower() == "nan")
    ].copy()

    merge_df = merge_df[merge_df["BD"].notna()]
    merge_df = merge_df[merge_df["BD"].astype(str).str.strip() != ""]
    merge_df = merge_df[merge_df["BD"].astype(str).str.lower() != "nan"]

    result_list = []

    for business_date in sorted(merge_df["快照日期"].drop_duplicates()):
        day_df = merge_df[merge_df["快照日期"] == business_date]

        for bd_name in sorted(day_df["BD"].drop_duplicates()):
            bd_day_df = day_df[day_df["BD"] == bd_name]

            deal_total = len(bd_day_df)
            deal_signup = len(bd_day_df[bd_day_df["是否报名"] == 1])
            deal_rate = round(deal_signup / deal_total * 100, 2) if deal_total > 0 else 0

            expand_df = bd_day_df[bd_day_df["是否重点门店"] == 1]
            expand_total = len(expand_df)

            expand_signup = len(bd_day_df[(bd_day_df["是否重点门店"] == 1) & (bd_day_df["是否膨胀报名"] == 1)])
            expand_rate = round(expand_signup / expand_total * 100, 2) if expand_total > 0 else 0

            result_list.append({
                "日期": business_date,
                "当前私海BD": bd_name,
                "deal总数": deal_total,
                "deal报名数": deal_signup,
                "deal覆盖率": deal_rate,
                "膨胀deal总数": expand_total,
                "膨胀deal报名数": expand_signup,
                "膨胀deal覆盖率": expand_rate,
            })

    return pd.DataFrame(result_list), unmatched_df


def _make_excel_bytes(city_report, bd_report=None, unmatched_df=None, shen_df=None, bd_mapping_df=None):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        city_report.to_excel(writer, index=False, sheet_name="神会员日报")
        if bd_report is not None and not bd_report.empty:
            bd_report.to_excel(writer, index=False, sheet_name="神会员BD日报")
        if unmatched_df is not None and not unmatched_df.empty:
            unmatched_df.to_excel(writer, index=False, sheet_name="未匹配BD门店")
        if bd_mapping_df is not None and not bd_mapping_df.empty:
            bd_mapping_df.to_excel(writer, index=False, sheet_name="BD档案库")
        if shen_df is not None and not shen_df.empty:
            shen_df.to_excel(writer, index=False, sheet_name="神会员历史数据")
    output.seek(0)
    return output.getvalue()


def render_shen_member_cloud_monitor(client, project_code):
    st.subheader("神会员数据监控")

    st.info(
        "首次使用请上传一次 BD门店明细.xlsx，系统会保存到云端BD档案库；"
        "后续神会员BD日报将自动读取云端BD档案，无需重复上传BD门店明细。"
    )

    st.markdown("### 一、云历史库状态")
    c1, c2 = st.columns(2)

    with c1:
        st.write("神会员云历史库")
        try:
            st.write(get_shen_member_status(client, project_code))

            if st.button("查看已上传日期", key="show_shen_uploaded_dates"):
                date_df = get_shen_uploaded_dates(client, project_code)
                if date_df.empty:
                    st.warning("当前项目暂无已上传日期。")
                else:
                    st.dataframe(date_df, use_container_width=True)

        except Exception as e:
            st.error("读取神会员历史库失败，请确认已执行建表SQL。")
            st.exception(e)
            return

    with c2:
        st.write("BD档案库")
        try:
            st.write(get_shop_master_status(client, project_code))
        except Exception as e:
            st.error("读取BD档案库失败，请确认已执行建表SQL。")
            st.exception(e)

    st.divider()

    st.markdown("### 二、上传/更新BD门店档案")
    st.info("BD门店无变更，可不上传。仅首次使用或BD门店调整时上传。")

    bd_file = st.file_uploader(
        "上传BD门店明细.xlsx",
        type=["xlsx", "xls"],
        key="bd_master_file"
    )

    if st.button("保存/更新BD档案库", type="primary"):
        if bd_file is None:
            st.error("请先上传BD门店明细.xlsx。")
        else:
            try:
                count = upsert_shop_master_to_cloud(client, project_code, bd_file)
                st.success(f"BD档案库保存成功，共 {count} 家门店。")
                st.write(get_shop_master_status(client, project_code))
            except Exception as e:
                st.error("BD档案库保存失败。")
                st.exception(e)

    with st.expander("危险操作：清空BD档案库"):
        st.warning("只会清空当前项目编码下的BD档案，不会删除神会员历史数据。")
        confirm_bd = st.checkbox("我确认要清空当前项目BD档案库")
        if confirm_bd and st.button("清空BD档案库", type="primary"):
            try:
                delete_shop_master(client, project_code)
                st.success(f"已清空项目 {project_code} 的BD档案库。")
                st.write(get_shop_master_status(client, project_code))
            except Exception as e:
                st.error("清空BD档案库失败。")
                st.exception(e)

    st.divider()

    st.markdown("### 三、上传神会员每日数据")
    shen_file = st.file_uploader("上传神会员Excel", type=["xlsx", "xls"], key="shen_member_cloud_file")

    if st.button("保存神会员数据到云历史库", type="primary"):
        if shen_file is None:
            st.error("请先上传神会员Excel。")
        else:
            try:
                count, date_list = upsert_shen_member_to_cloud(client, project_code, shen_file)
                st.success(f"神会员数据已保存/覆盖，共处理 {count} 行，日期：{', '.join(date_list)}")
                st.write(get_shen_member_status(client, project_code))
            except Exception as e:
                st.error("神会员数据保存失败。")
                st.exception(e)

    with st.expander("危险操作：清空神会员云历史库"):
        st.warning("只会清空当前项目编码下的神会员数据，不会删除BD档案库。")
        confirm_shen = st.checkbox("我确认要清空当前项目神会员云历史库")
        if confirm_shen and st.button("清空神会员云历史库", type="primary"):
            try:
                delete_shen_member_data(client, project_code)
                st.success(f"已清空项目 {project_code} 的神会员云历史库。")
                st.write(get_shen_member_status(client, project_code))
            except Exception as e:
                st.error("清空神会员云历史库失败。")
                st.exception(e)

    st.divider()

    st.markdown("### 四、神会员历史监控")

    shen_df = load_shen_member_from_cloud(client, project_code)

    if shen_df.empty:
        st.warning("当前项目暂无神会员历史数据。")
        return

    shen_df["快照日期"] = pd.to_datetime(shen_df["快照日期"], errors="coerce").dt.strftime("%Y-%m-%d")

    min_date = pd.to_datetime(shen_df["快照日期"]).min().date()
    max_date = pd.to_datetime(shen_df["快照日期"]).max().date()

    col_start, col_end = st.columns(2)
    start_date = col_start.date_input("神会员开始日期", value=min_date, min_value=min_date, max_value=max_date, key="shen_start_date")
    end_date = col_end.date_input("神会员结束日期", value=max_date, min_value=min_date, max_value=max_date, key="shen_end_date")

    filtered_df = shen_df[
        (pd.to_datetime(shen_df["快照日期"]).dt.date >= start_date)
        &
        (pd.to_datetime(shen_df["快照日期"]).dt.date <= end_date)
    ].copy()

    if filtered_df.empty:
        st.warning("当前日期范围内没有神会员数据。")
        return

    city_report = calc_shen_city_report(filtered_df)

    st.markdown("### 五、神会员日报")
    st.dataframe(city_report, use_container_width=True)

    if not city_report.empty:
        latest = city_report.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("最新deal总数", int(latest["deal总数"]))
        c2.metric("最新deal覆盖率", f'{latest["deal覆盖率"]}%')
        c3.metric("最新膨胀deal总数", int(latest["膨胀deal总数"]))
        c4.metric("最新膨胀覆盖率", f'{latest["膨胀deal覆盖率"]}%')

        st.markdown("### 六、覆盖率趋势")
        trend_df = city_report.set_index("日期")[["deal覆盖率", "膨胀deal覆盖率"]]
        st.line_chart(trend_df, use_container_width=True)

    st.divider()

    st.markdown("### 七、神会员BD日报（自动读取BD档案库）")

    bd_mapping_df = load_shop_master_from_cloud(client, project_code)

    if bd_mapping_df.empty:
        st.warning("当前项目暂无BD档案，请先在上方上传BD门店明细.xlsx。")
        bd_report = pd.DataFrame()
        unmatched_df = filtered_df.copy()
    else:
        st.write({
            "BD档案映射门店数": len(bd_mapping_df),
            "BD数": bd_mapping_df["BD"].nunique()
        })

        bd_report, unmatched_df = calc_shen_bd_report(filtered_df, bd_mapping_df)

        if bd_report.empty:
            st.warning("未生成BD维度数据，可能是神会员门店名称与BD档案库门店名称未匹配。")
        else:
            st.dataframe(bd_report, use_container_width=True)

            latest_date = bd_report["日期"].max()
            latest_bd = bd_report[bd_report["日期"] == latest_date].copy()

            st.markdown(f"### 八、BD覆盖率排行（{latest_date}）")
            rank_df = latest_bd.sort_values(by=["deal覆盖率", "膨胀deal覆盖率"], ascending=False)
            st.dataframe(rank_df, use_container_width=True)

            st.markdown("### 九、BD覆盖率图")
            chart_df = rank_df.set_index("当前私海BD")[["deal覆盖率", "膨胀deal覆盖率"]]
            st.bar_chart(chart_df, use_container_width=True)

        if unmatched_df is not None and not unmatched_df.empty:
            st.warning(f"有 {len(unmatched_df)} 行神会员数据未匹配到BD。")
            with st.expander("查看未匹配BD门店"):
                if "门店名称" in unmatched_df.columns:
                    st.dataframe(unmatched_df[["快照日期", "门店名称"]].drop_duplicates(), use_container_width=True)
                else:
                    st.dataframe(unmatched_df, use_container_width=True)

    excel_bytes = _make_excel_bytes(
        city_report=city_report,
        bd_report=bd_report,
        unmatched_df=unmatched_df,
        shen_df=filtered_df,
        bd_mapping_df=bd_mapping_df
    )

    st.download_button(
        "下载神会员监控Excel",
        data=excel_bytes,
        file_name="神会员监控日报.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
