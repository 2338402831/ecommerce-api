from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
import uvicorn

app = FastAPI(
    title="电商分析结果查询API",
    description="用于查询MongoDB中存储的电商分析结果",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB连接配置
MONGO_URI = "mongodb://admin:password@59.110.113.0:27017/"
DB_NAME = "ecommerce_analysis"
COLLECTIONS = {
    "categories": "categories",
    "user_groups": "user_groups",
    "questions": "questions",
    "answers": "answers",
    "brands": "brands",
    "combined": "combined_results"
}

# 初始化MongoDB连接
try:
    mongo_client = MongoClient(MONGO_URI, authSource="admin", serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')  # 测试连接
    db = mongo_client[DB_NAME]
    print("✅ MongoDB连接成功")
except Exception as e:
    print(f"❌ MongoDB连接失败: {e}")
    raise RuntimeError("无法连接MongoDB")


class QueryResult(BaseModel):
    id: str
    url: str
    created_at: datetime
    updated_at: datetime
    data: dict


class CollectionStats(BaseModel):
    collection_name: str
    document_count: int
    earliest_record: Optional[datetime]
    latest_record: Optional[datetime]


@app.get("/", tags=["根路径"])
async def root():
    """API基本信息"""
    return {
        "service": "电商分析结果查询API",
        "status": "运行中",
        "available_collections": list(COLLECTIONS.values())
    }


@app.get("/collections/stats", response_model=List[CollectionStats], tags=["元数据"])
async def get_collections_stats():
    """获取所有集合的统计信息"""
    stats = []
    for col_name in COLLECTIONS.values():
        collection = db[col_name]
        count = collection.count_documents({})
        earliest = collection.find_one(sort=[("created_at", 1)])
        latest = collection.find_one(sort=[("created_at", -1)])

        stats.append(CollectionStats(
            collection_name=col_name,
            document_count=count,
            earliest_record=earliest["created_at"] if earliest else None,
            latest_record=latest["created_at"] if latest else None
        ))
    return stats


@app.get("/results/latest", response_model=Dict[str, List[QueryResult]], tags=["查询"])
async def get_latest_results(limit: int = Query(5, ge=1, le=50)):
    """获取各集合最新的分析结果"""
    results = {}
    for col_type, col_name in COLLECTIONS.items():
        collection = db[col_name]
        docs = collection.find().sort("created_at", -1).limit(limit)
        results[col_type] = [
            QueryResult(
                id=str(doc["_id"]),
                url=doc["url"],
                created_at=doc["created_at"],
                updated_at=doc["updated_at"],
                data=doc["data"]
            )
            for doc in docs
        ]
    return results


@app.get("/results/by-url", response_model=Dict[str, List[QueryResult]], tags=["查询"])
async def get_results_by_url(url: str):
    """根据URL查询所有相关分析结果"""
    if not url.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="URL必须以http://或https://开头")

    results = {}
    for col_type, col_name in COLLECTIONS.items():
        collection = db[col_name]
        docs = collection.find({"url": url}).sort("created_at", -1)
        results[col_type] = [
            QueryResult(
                id=str(doc["_id"]),
                url=doc["url"],
                created_at=doc["created_at"],
                updated_at=doc["updated_at"],
                data=doc["data"]
            )
            for doc in docs
        ]
    return results


@app.get("/results/{collection_name}", response_model=List[QueryResult], tags=["查询"])
async def get_collection_results(
        collection_name: str,
        limit: int = Query(10, ge=1, le=100),
        skip: int = Query(0, ge=0)
):
    """查询指定集合的所有结果"""
    if collection_name not in COLLECTIONS.values():
        raise HTTPException(status_code=404, detail="集合不存在")

    collection = db[collection_name]
    docs = collection.find().sort("created_at", -1).skip(skip).limit(limit)

    return [
        QueryResult(
            id=str(doc["_id"]),
            url=doc["url"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            data=doc["data"]
        )
        for doc in docs
    ]


@app.get("/result/{document_id}", response_model=QueryResult, tags=["查询"])
async def get_single_result(document_id: str):
    """根据文档ID获取单个分析结果"""
    try:
        obj_id = ObjectId(document_id)
    except:
        raise HTTPException(status_code=400, detail="无效的文档ID格式")

    # 在所有集合中搜索该ID
    for col_name in COLLECTIONS.values():
        doc = db[col_name].find_one({"_id": obj_id})
        if doc:
            return QueryResult(
                id=str(doc["_id"]),
                url=doc["url"],
                created_at=doc["created_at"],
                updated_at=doc["updated_at"],
                data=doc["data"]
            )

    raise HTTPException(status_code=404, detail="未找到指定文档")


@app.get("/health", tags=["健康检查"])
async def health_check():
    """服务健康检查"""
    try:
        mongo_client.admin.command('ping')
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")