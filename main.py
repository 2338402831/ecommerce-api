from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Any, Optional
import uvicorn
from datetime import datetime
import time
from contextlib import asynccontextmanager
from ecommerce_analyzer import EcommerceAnalyzer  # 假设原代码在ecommerce_analyzer.py文件中

# 创建FastAPI应用实例
app = FastAPI(
    title="电商网站分析API",
    description="分析电商网站的产品类别、目标用户、生成相关问题和回答，并提取品牌信息",
    version="1.0.0"
)

# 配置CORS，允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 请求模型定义
class AnalysisRequest(BaseModel):
    url: str
    questions_per_group: int = 3
    answers_per_question: int = 3
    use_ai_analysis: bool = True


# 回答项模型
class AnswerItem(BaseModel):
    answer: str
    brands: List[str]


# 问题模型
class QuestionModel(BaseModel):
    question: str
    answers: List[AnswerItem]


# 用户群体模型
class UserGroupModel(BaseModel):
    user_group: str
    questions: List[QuestionModel]


# 类别模型
class CategoryModel(BaseModel):
    category: str
    user_groups: List[UserGroupModel]


# 响应模型定义
class AnalysisResponse(BaseModel):
    url: str
    categories: List[str]
    analysis_result: List[CategoryModel]
    timestamp: datetime
    elapsed_time: float


# 初始化分析器实例（全局单例模式）
analyzer = None


# 使用lifespan处理程序替代on_event
@asynccontextmanager
async def lifespan(app: FastAPI):
    global analyzer
    # 启动事件
    try:
        # 从环境变量获取API密钥，也可以在这里硬编码或从配置文件读取
        zhipuai_api_key = "125b8d8dfcf89504858730426ec28748.2IW4l1oHokX0hLeA"
        analyzer = EcommerceAnalyzer(zhipuai_api_key=zhipuai_api_key, max_workers=5)
        print("API服务启动，分析器初始化完成")
    except Exception as e:
        print(f"服务启动失败: {e}")
        # 可以选择在这里抛出异常，阻止应用启动
        raise

    # 生成应用实例
    yield

    # 关闭事件
    if analyzer and analyzer.mongo_client:
        analyzer.mongo_client.close()
        print("MongoDB连接已关闭")
    print("API服务关闭")


# 应用实例使用lifespan处理程序
app = FastAPI(lifespan=lifespan)


@app.get("/", tags=["根路径"])
async def read_root():
    """根路径，返回API基本信息"""
    return {
        "message": "电商网站分析API服务正在运行",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.post("/analyze", response_model=AnalysisResponse, tags=["分析功能"])
async def analyze_website(request: AnalysisRequest):
    """
    分析指定电商网站的主要功能端点
    - **url**: 要分析的电商网站URL
    - **questions_per_group**: 每组用户生成的问题数量，默认3个
    - **answers_per_question**: 每个问题生成的回答数量，默认3个
    - **use_ai_analysis**: 是否使用AI分析模式，默认True
    """
    start_time = time.time()

    try:
        # 验证URL格式
        if not request.url.startswith(('http://', 'https://')):
            raise HTTPException(status_code=400, detail="无效的URL格式，必须包含http或https")

        print(f"开始分析网站: {request.url}")

        # 执行分析
        analysis_result = analyzer.analyze_website(
            url=request.url,
            questions_per_group=request.questions_per_group,
            answers_per_question=request.answers_per_question,
            use_ai_analysis=request.use_ai_analysis
        )

        # 提取类别信息（假设从分析结果中获取）
        categories_result = analyzer.extract_categories(request.url)
        categories = categories_result.get("categories", [])

        # 整理响应数据
        response_data = {
            "url": request.url,
            "categories": categories,
            "analysis_result": convert_to_response_model(analysis_result),
            "timestamp": datetime.now(),
            "elapsed_time": time.time() - start_time
        }

        return response_data

    except Exception as e:
        error_msg = f"网站分析失败: {str(e)}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


def convert_to_response_model(analysis_result: Dict[str, Any]) -> List[CategoryModel]:
    """将分析结果转换为响应模型格式"""
    response_models = []

    for category, user_data in analysis_result.items():
        user_groups = []

        for user_group, qa_data in user_data.items():
            questions = []

            for question, answers in qa_data.items():
                answer_items = [
                    AnswerItem(answer=item["answer"], brands=item["brands"])
                    for item in answers
                ]
                questions.append(QuestionModel(question=question, answers=answer_items))

            user_groups.append(UserGroupModel(user_group=user_group, questions=questions))

        response_models.append(CategoryModel(category=category, user_groups=user_groups))

    return response_models


@app.get("/health", tags=["健康检查"])
async def health_check():
    """健康检查端点，用于容器化部署时的健康检查"""
    try:
        # 检查MongoDB连接
        analyzer.mongo_client.admin.command('ping')
        return {
            "status": "healthy",
            "timestamp": datetime.now(),
            "message": "服务运行正常"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(),
            "message": f"服务异常: {str(e)}"
        }


# 主函数，用于直接运行API服务
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")