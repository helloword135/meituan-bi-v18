import re
import pandas as pd
import streamlit as st
from datetime import date
from supabase import create_client

TABLE_NAME = "meituan_history"
AUTH_TABLE_NAME = "auth_codes"

def get_supabase_client():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = text.replace("－", "-").replace("–", "-").replace("—", "-")
    return text.strip()

def verify_auth_code(client, code, project_code_input):
    if not code or not clean_text(code):
        raise Exception("请输入授权码。")
    if not project_code_input or not clean_text(project_code_input):
        raise Exception("请输入项目/城市编码。")

    code = clean_text(code)
    project_code_input = clean_text(project_code_input)

    # 使用 V17.1 已验证成功的方式：读取全部授权码后本地匹配
    resp = client.table(AUTH_TABLE_NAME).select("*").execute()
    rows = resp.data or []

    matched = None
    for row in rows:
        db_code = clean_text(row.get("code"))
        if db_code == code:
            matched = row
            break

    if matched is None:
        raise Exception("授权码不存在。")

    row = matched

    if not row.get("is_active", False):
        raise Exception("授权码已停用，请联系管理员。")

    expire_date_text = row.get("expire_date")
    if not expire_date_text:
        raise Exception("授权码未设置到期时间，请联系管理员。")

    expire_date = pd.to_datetime(expire_date_text).date()
    today = date.today()

    if expire_date < today:
        raise Exception(f"授权码已过期，到期时间：{expire_date}。")

    bound_project_code = clean_text(row.get("project_code"))

    if bound_project_code:
        if bound_project_code != project_code_input:
            raise Exception(f"授权码与当前项目/城市编码不匹配。授权码绑定的是：{bound_project_code}")
        project_code = bound_project_code
    else:
        project_code = project_code_input

    return {
        "code": code,
        "project_code": project_code,
        "expire_date": str(expire_date),
        "remark": row.get("remark")
    }

def normalize_daily_file(uploaded_file):
    df = pd.read_excel(uploaded_file)
    if "快照日期" not in df.columns:
        filename = getattr(uploaded_file, "name", "")
        m = re.search(r"(20\d{2}[-_/\.]\d{1,2}[-_/\.]\d{1,2})", filename)
        if not m:
            raise Exception("上传文件缺少【快照日期】字段，且文件名中无法识别日期。请把文件命名为 2026-05-15.xlsx 这种格式。")
        date_text = m.group(1).replace("_", "-").replace("/", "-").replace(".", "-")
        df["快照日期"] = pd.to_datetime(date_text)
    else:
        df["快照日期"] = pd.to_datetime(df["快照日期"])
    return df

def upsert_daily_to_cloud(client, project_code, uploaded_file):
    df = normalize_daily_file(uploaded_file)
    if "门店名称" not in df.columns:
        raise Exception("当天导出文件缺少字段：门店名称")

    df["快照日期"] = pd.to_datetime(df["快照日期"]).dt.strftime("%Y-%m-%d")
    date_list = sorted(df["快照日期"].dropna().unique().tolist())

    for date_text in date_list:
        client.table(TABLE_NAME).delete().eq("project_code", project_code).eq("snapshot_date", date_text).execute()

    records = []
    for _, row in df.iterrows():
        snapshot_date = row["快照日期"]
        shop_name = "" if pd.isna(row.get("门店名称")) else str(row.get("门店名称"))

        data_json = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                data_json[col] = None
            elif isinstance(val, pd.Timestamp):
                data_json[col] = val.strftime("%Y-%m-%d")
            else:
                data_json[col] = val.item() if hasattr(val, "item") else val

        records.append({
            "project_code": project_code,
            "snapshot_date": snapshot_date,
            "shop_name": shop_name,
            "data": data_json
        })

    batch_size = 500
    for i in range(0, len(records), batch_size):
        client.table(TABLE_NAME).insert(records[i:i + batch_size]).execute()

    return len(records)

def load_history_from_cloud(client, project_code):
    all_rows = []
    start = 0
    page_size = 1000

    while True:
        resp = (
            client.table(TABLE_NAME)
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

    df = pd.DataFrame([r["data"] for r in all_rows])
    if "快照日期" in df.columns:
        df["快照日期"] = pd.to_datetime(df["快照日期"])
    return df

def get_history_status(client, project_code):
    df = load_history_from_cloud(client, project_code)
    if df.empty:
        return {"项目编码": project_code, "状态": "暂无历史数据"}

    return {
        "项目编码": project_code,
        "历史记录数": len(df),
        "门店数": df["门店名称"].nunique() if "门店名称" in df.columns else None,
        "最早日期": str(pd.to_datetime(df["快照日期"]).min().date()) if "快照日期" in df.columns else None,
        "最新日期": str(pd.to_datetime(df["快照日期"]).max().date()) if "快照日期" in df.columns else None,
    }

def delete_project_data(client, project_code):
    client.table(TABLE_NAME).delete().eq("project_code", project_code).execute()

def calculate_dashboard(df, bd_df):
    shop_col = "门店名称"
    level_col = "门店分层"
    bd_col = "BD"
    gtv_col = "实付验证GTV"
    smart_col = "智能点餐实付验证GTV"
    new_flag_col = "是否本月新签门店"
    new_gtv_col = "新签门店实付验证GTV"
    active_col = "是否本月新签门店动销达标"
    shelf_col = "是否货架达标"
    decorate_col = "是否装修头图达标"

    if bd_col in df.columns:
        df = df.drop(columns=[bd_col])

    need_cols = [
        shop_col, level_col, gtv_col, smart_col, new_flag_col, new_gtv_col,
        active_col, shelf_col, decorate_col, "快照日期"
    ]

    missing_cols = [col for col in need_cols if col not in df.columns]
    if missing_cols:
        raise Exception("云历史库缺少字段：" + "、".join(missing_cols))

    bd_need_cols = [shop_col, bd_col]
    bd_missing_cols = [col for col in bd_need_cols if col not in bd_df.columns]
    if bd_missing_cols:
        raise Exception("BD门店明细缺少字段：" + "、".join(bd_missing_cols))

    bd_df = bd_df[[shop_col, bd_col]].drop_duplicates()
    df = df.merge(bd_df, on=shop_col, how="left")
    df["快照日期"] = pd.to_datetime(df["快照日期"])

    agg_dict = {
        level_col: "first",
        gtv_col: "max",
        smart_col: "max",
        new_flag_col: "max",
        new_gtv_col: "max",
        active_col: "max",
        shelf_col: "max",
        decorate_col: "max",
        bd_col: "first"
    }

    df = df.groupby(["快照日期", shop_col], as_index=False).agg(agg_dict)
    df = df.sort_values(by=[shop_col, "快照日期"])

    df["上一日累计GTV"] = df.groupby(shop_col)[gtv_col].shift(1)
    df["每日新增GTV"] = (df[gtv_col] - df["上一日累计GTV"]).fillna(df[gtv_col])

    df["上一日智能点餐"] = df.groupby(shop_col)[smart_col].shift(1)
    df["每日新增智能点餐"] = (df[smart_col] - df["上一日智能点餐"]).fillna(df[smart_col])

    df["日期文本"] = df["快照日期"].dt.strftime("%Y-%m-%d")

    gtv_pivot = df.pivot_table(
        index=shop_col,
        columns="日期文本",
        values="每日新增GTV",
        aggfunc="sum",
        fill_value=0
    )
    gtv_pivot.columns = [f"{col}新增GTV" for col in gtv_pivot.columns]

    smart_pivot = df.pivot_table(
        index=shop_col,
        columns="日期文本",
        values="每日新增智能点餐",
        aggfunc="sum",
        fill_value=0
    )
    smart_pivot.columns = [f"{col}新增智能点餐" for col in smart_pivot.columns]

    latest_date = df["快照日期"].max()
    latest_df = df[df["快照日期"] == latest_date].copy()

    latest_df["月度累计GTV"] = latest_df[gtv_col]
    latest_df["月度累计智能点餐"] = latest_df[smart_col]
    latest_df["月度累计新签"] = latest_df[new_flag_col]
    latest_df["新签门店实付验证GTV"] = latest_df[new_gtv_col]

    latest_df = latest_df[[
        bd_col, shop_col, level_col, "月度累计GTV", "月度累计智能点餐",
        "月度累计新签", "新签门店实付验证GTV", active_col, shelf_col, decorate_col
    ]]

    result = latest_df.merge(gtv_pivot, on=shop_col, how="left")
    result = result.merge(smart_pivot, on=shop_col, how="left")
    return result
