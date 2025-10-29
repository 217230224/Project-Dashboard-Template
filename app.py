import os
import datetime as dt
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from sqlalchemy import create_engine, text
from pymongo import MongoClient

# 加载环境变量
load_dotenv()

# PostgreSQL  schema 配置（使用音乐数据库的schema）
PG_SCHEMA = os.getenv("PG_SCHEMA", "music_house")  # 对应你的音乐数据库的schema
def qualify(sql: str) -> str:
    return sql.replace("{S}.", f"{PG_SCHEMA}.")

# 数据库查询配置（PostgreSQL + MongoDB）
CONFIG = {
    "postgres": {
        "enabled": True,
        "uri": os.getenv("PG_URI", "postgresql+psycopg2://postgres:password@localhost:5432/music_house"),  # 音乐数据库连接
        "queries": {
            # 用户角色：普通用户（用户视角）
            "用户：我的听歌历史（表格）": {
                "sql": """
                    SELECT s.song_name, a.artist_name, 
                           TO_CHAR(l.play_time, 'YYYY-MM-DD HH24:MI') AS play_time,
                           l.play_duration_ms / 1000 AS play_seconds,
                           l.device_type
                    FROM {S}.user_listening_history l
                    JOIN {S}.songs s ON l.song_id = s.song_id
                    JOIN {S}.artists a ON s.artist_id = a.artist_id
                    WHERE l.user_id = :user_id
                    ORDER BY l.play_time DESC
                    LIMIT 20;
                """,
                "chart": {"type": "table"},
                "tags": ["user"],
                "params": ["user_id"]
            },
            "用户：我喜欢的歌曲（表格）": {
                "sql": """
                    SELECT s.song_name, a.artist_name, s.genre[1] AS main_genre
                    FROM {S}.user_interactions i
                    JOIN {S}.songs s ON i.song_id = s.song_id
                    JOIN {S}.artists a ON s.artist_id = a.artist_id
                    WHERE i.user_id = :user_id
                      AND i.interaction_type = 'like'
                    ORDER BY i.interaction_time DESC;
                """,
                "chart": {"type": "table"},
                "tags": ["user"],
                "params": ["user_id"]
            },
            "用户：我的听歌时长统计（柱状图）": {
                "sql": """
                    SELECT TO_CHAR(l.play_time, 'YYYY-MM-DD') AS play_date,
                           SUM(l.play_duration_ms) / 3600000 AS total_hours
                    FROM {S}.user_listening_history l
                    WHERE l.user_id = :user_id
                      AND l.play_time >= CURRENT_DATE - INTERVAL ':days days'
                    GROUP BY play_date
                    ORDER BY play_date;
                """,
                "chart": {"type": "bar", "x": "play_date", "y": "total_hours"},
                "tags": ["user"],
                "params": ["user_id", "days"]
            },

            # 用户角色：艺术家（创作者视角）
            "艺术家：我的歌曲播放量（柱状图）": {
                "sql": """
                    SELECT s.song_name, COUNT(l.history_id) AS play_count
                    FROM {S}.songs s
                    LEFT JOIN {S}.user_listening_history l ON s.song_id = l.song_id
                    WHERE s.artist_id = :artist_id
                    GROUP BY s.song_name
                    ORDER BY play_count DESC;
                """,
                "chart": {"type": "bar", "x": "song_name", "y": "play_count"},
                "tags": ["artist"],
                "params": ["artist_id"]
            },
            "艺术家：我的歌曲互动统计（饼图）": {
                "sql": """
                    SELECT i.interaction_type, COUNT(i.interaction_id) AS count
                    FROM {S}.user_interactions i
                    JOIN {S}.songs s ON i.song_id = s.song_id
                    WHERE s.artist_id = :artist_id
                    GROUP BY i.interaction_type;
                """,
                "chart": {"type": "pie", "names": "interaction_type", "values": "count"},
                "tags": ["artist"],
                "params": ["artist_id"]
            },

            # 用户角色：平台管理员（管理员视角）
            "管理员：平台用户统计（表格）": {
                "sql": """
                    SELECT COUNT(*) AS total_users,
                           SUM(CASE WHEN is_premium THEN 1 ELSE 0 END) AS premium_users,
                           ROUND(AVG(EXTRACT(DAY FROM CURRENT_TIMESTAMP - signup_date)), 1) AS avg_days_active
                    FROM {S}.users;
                """,
                "chart": {"type": "table"},
                "tags": ["admin"]
            },
            "管理员：热门歌曲TOP10（柱状图）": {
                "sql": """
                    SELECT s.song_name, a.artist_name, COUNT(l.history_id) AS play_count
                    FROM {S}.songs s
                    JOIN {S}.artists a ON s.artist_id = a.artist_id
                    LEFT JOIN {S}.user_listening_history l ON s.song_id = l.song_id
                    GROUP BY s.song_name, a.artist_name
                    ORDER BY play_count DESC
                    LIMIT 10;
                """,
                "chart": {"type": "bar", "x": "song_name", "y": "play_count", "color": "artist_name"},
                "tags": ["admin"]
            },
            "管理员：用户地区分布（饼图）": {
                "sql": """
                    SELECT country, COUNT(*) AS user_count
                    FROM {S}.users
                    WHERE country IS NOT NULL
                    GROUP BY country
                    ORDER BY user_count DESC;
                """,
                "chart": {"type": "pie", "names": "country", "values": "user_count"},
                "tags": ["admin"]
            }
        }
    },

    "mongo": {
        "enabled": True,
        "uri": os.getenv("MONGO_URI", "mongodb://localhost:27017"),  # MongoDB连接
        "db_name": os.getenv("MONGO_DB", "music_house"),  # MongoDB数据库名
        "queries": {
            # 流媒体事件统计（按事件类型）
            "流媒体：事件类型分布（饼图）": {
                "collection": "stream_events",  # 对应你的stream事件集合
                "aggregate": [
                    {"$group": {"_id": "$event_type", "count": {"$count": {}}}},
                    {"$sort": {"count": -1}}
                ],
                "chart": {"type": "pie", "names": "_id", "values": "count"}
            },
            # 最近7天网络质量统计
            "流媒体：近7天网络速度分布（柱状图）": {
                "collection": "stream_events",
                "aggregate": [
                    {"$match": {
                        "event_time": {"$gte": dt.datetime.utcnow() - dt.timedelta(days=7)},
                        "network.speed_mbps": {"$exists": True}
                    }},
                    {"$project": {
                        "date": {"$dateTrunc": {"date": "$event_time", "unit": "day"}},
                        "speed": "$network.speed_mbps"
                    }},
                    {"$group": {"_id": "$date", "avg_speed": {"$avg": "$speed"}}},
                    {"$sort": {"_id": 1}}
                ],
                "chart": {"type": "line", "x": "_id", "y": "avg_speed"}
            },
            # 用户互动设备分布
            "用户互动：设备类型统计（柱状图）": {
                "collection": "user_engagement",  # 对应你的互动集合
                "aggregate": [
                    {"$group": {"_id": "$device", "count": {"$count": {}}}},
                    {"$sort": {"count": -1}}
                ],
                "chart": {"type": "bar", "x": "_id", "y": "count"}
            },
            # 推荐来源效果统计
            "推荐系统：推荐来源有效性（表格）": {
                "collection": "user_engagement",
                "aggregate": [
                    {"$match": {"recommendation_source": {"$exists": True}}},
                    {"$group": {
                        "_id": "$recommendation_source",
                        "total": {"$count": {}},
                        "share_count": {"$sum": {"$cond": [{"$eq": ["$engagement_type", "share"]}, 1, 0]}}
                    }},
                    {"$project": {
                        "推荐来源": "$_id",
                        "总互动量": "$total",
                        "分享率": {"$round": [{"$divide": ["$share_count", "$total"]}, 2]}
                    }},
                    {"$sort": {"总互动量": -1}}
                ],
                "chart": {"type": "table"}
            }
        }
    }
}

# 页面配置
st.set_page_config(page_title="音乐平台数据仪表盘", layout="wide")
st.title("🎵 音乐平台数据仪表盘 (PostgreSQL + MongoDB)")

def metric_row(metrics: dict):
    cols = st.columns(len(metrics))
    for (k, v), c in zip(metrics.items(), cols):
        c.metric(k, v)

# PostgreSQL 连接与查询
@st.cache_resource
def get_pg_engine(uri: str):
    return create_engine(uri, pool_pre_ping=True, future=True)

@st.cache_data(ttl=60)
def run_pg_query(_engine, sql: str, params: dict | None = None):
    with _engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})

# MongoDB 连接与查询
@st.cache_resource
def get_mongo_client(uri: str):
    return MongoClient(uri)

def mongo_overview(client: MongoClient, db_name: str):
    info = client.server_info()
    db = client[db_name]
    colls = db.list_collection_names()
    stats = db.command("dbstats")
    total_docs = sum(db[c].estimated_document_count() for c in colls) if colls else 0
    return {
        "数据库名称": db_name,
        "集合数量": f"{len(colls):,}",
        "总文档数(估计)": f"{total_docs:,}",
        "存储大小": f"{round(stats.get('storageSize',0)/1024/1024,1)} MB",
        "MongoDB版本": info.get("version", "unknown")
    }

@st.cache_data(ttl=60)
def run_mongo_aggregate(_client, db_name: str, coll: str, stages: list):
    db = _client[db_name]
    docs = list(db[coll].aggregate(stages, allowDiskUse=True))
    return pd.json_normalize(docs) if docs else pd.DataFrame()

# 图表渲染
def render_chart(df: pd.DataFrame, spec: dict):
    if df.empty:
        st.info("没有查询到数据。")
        return
    ctype = spec.get("type", "table")
    
    # 自动解析日期格式
    for c in df.columns:
        if df[c].dtype == "object":
            try:
                df[c] = pd.to_datetime(df[c])
            except Exception:
                pass

    if ctype == "table":
        st.dataframe(df, use_container_width=True)
    elif ctype == "line":
        st.plotly_chart(px.line(df, x=spec["x"], y=spec["y"], title="趋势图"), use_container_width=True)
    elif ctype == "bar":
        color = spec.get("color")
        if color:
            st.plotly_chart(px.bar(df, x=spec["x"], y=spec["y"], color=color, title="柱状图"), use_container_width=True)
        else:
            st.plotly_chart(px.bar(df, x=spec["x"], y=spec["y"], title="柱状图"), use_container_width=True)
    elif ctype == "pie":
        st.plotly_chart(px.pie(df, names=spec["names"], values=spec["values"], title="饼图"), use_container_width=True)
    elif ctype == "treemap":
        st.plotly_chart(px.treemap(df, path=spec["path"], values=spec["values"], title="树状图"), use_container_width=True)
    else:
        st.dataframe(df, use_container_width=True)

# 侧边栏配置
with st.sidebar:
    st.header("数据库连接")
    pg_uri = st.text_input("PostgreSQL 连接地址", CONFIG["postgres"]["uri"])
    mongo_uri = st.text_input("MongoDB 连接地址", CONFIG["mongo"]["uri"])
    mongo_db = st.text_input("MongoDB 数据库名", CONFIG["mongo"]["db_name"])
    st.divider()
    auto_run = st.checkbox("选择后自动执行查询", value=False)

    st.header("角色与参数")
    role = st.selectbox("用户角色", ["user", "artist", "admin", "all"], index=3)
    user_id = st.number_input("用户ID", min_value=1, value=1, step=1)
    artist_id = st.number_input("艺术家ID", min_value=1, value=1, step=1)
    days = st.slider("近N天数据", 1, 30, 7)

    PARAMS_CTX = {
        "user_id": int(user_id),
        "artist_id": int(artist_id),
        "days": int(days)
    }

# PostgreSQL 仪表盘部分
st.subheader("📊 PostgreSQL 数据")
try:
    eng = get_pg_engine(pg_uri)
    with st.expander("运行PostgreSQL查询", expanded=True):
        def filter_queries_by_role(qdict: dict, role: str) -> dict:
            def ok(tags):
                t = [s.lower() for s in (tags or ["all"])]
                return "all" in t or role.lower() in t
            return {name: q for name, q in qdict.items() if ok(q.get("tags"))}

        pg_all = CONFIG["postgres"]["queries"]
        pg_q = filter_queries_by_role(pg_all, role)
        names = list(pg_q.keys()) or ["(该角色无可用查询)"]
        sel = st.selectbox("选择查询", names, key="pg_sel")

        if sel in pg_q:
            q = pg_q[sel]
            sql = qualify(q["sql"])
            st.code(sql, language="sql")

            run = auto_run or st.button("▶ 执行查询", key="pg_run")
            if run:
                wanted = q.get("params", [])
                params = {k: PARAMS_CTX[k] for k in wanted}
                df = run_pg_query(eng, sql, params=params)
                render_chart(df, q["chart"])
        else:
            st.info("无符合该角色的查询。")
except Exception as e:
    st.error(f"PostgreSQL 错误: {e}")

# MongoDB 仪表盘部分
if CONFIG["mongo"]["enabled"]:
    st.subheader("🍃 MongoDB 数据")
    try:
        mongo_client = get_mongo_client(mongo_uri)
        metric_row(mongo_overview(mongo_client, mongo_db))

        with st.expander("运行MongoDB聚合查询", expanded=True):
            mongo_query_names = list(CONFIG["mongo"]["queries"].keys())
            selm = st.selectbox("选择聚合查询", mongo_query_names, key="mongo_sel")
            q = CONFIG["mongo"]["queries"][selm]
            st.write(f"**集合:** `{q['collection']}`")
            st.code(str(q["aggregate"]), language="python")
            runm = auto_run or st.button("▶ 执行聚合", key="mongo_run")
            if runm:
                dfm = run_mongo_aggregate(mongo_client, mongo_db, q["collection"], q["aggregate"])
                render_chart(dfm, q["chart"])
    except Exception as e:
        st.error(f"MongoDB 错误: {e}")