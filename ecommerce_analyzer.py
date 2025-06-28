import concurrent.futures
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Any
import re
from zhipuai import ZhipuAI
import os
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from dataclasses import dataclass
from collections import defaultdict
from pymongo import MongoClient
from datetime import datetime
from typing import Dict, List, Any, Optional
import time
import tqdm
from datetime import datetime
import pickle
import os

@dataclass
class AIResponse:
    question: str = ""
    answers: List[Dict[str, Any]] = None


class EcommerceAnalyzer:
    def __init__(self, zhipuai_api_key: str = None, max_workers: int = 10, use_ai_analysis: bool = False):
        # 产品大类与人群映射（保留规则库）
        self.category_user_mapping = {
            '服装': {
                'patterns': ['服装|服饰|衣服|clothing|apparel|wear|上衣|下装|外套|T恤|卫衣'],
                'user_groups': [
                    ('男性', ['男|男士|男性|men']),
                    ('女性', ['女|女士|女性|women']),
                    ('青少年', ['青少年|少年|teen|youth']),
                    ('儿童', ['儿童|小孩|kids|children']),
                    ('运动员', ['运动员|运动|athlete']),
                    ('时尚达人', ['时尚|潮流|fashion|trendy']),
                    ('商务人士', ['商务|正装|business|formal'])
                ]
            },
            '鞋类': {
                'patterns': ['鞋|靴|footwear|sneaker|shoes'],
                'user_groups': [
                    ('跑步爱好者', ['跑步|running']),
                    ('篮球爱好者', ['篮球|basketball']),
                    ('足球爱好者', ['足球|soccer']),
                    ('健身人士', ['健身|训练|training']),
                    ('户外爱好者', ['户外|登山|hiking|outdoor']),
                    ('时尚潮人', ['时尚|潮流|fashion|trendy']),
                    ('日常穿着者', ['休闲|日常|casual|walking'])
                ]
            },
            '运动器材': {
                'patterns': ['器材|装备|equipment|gear'],
                'user_groups': [
                    ('专业运动员', ['专业|pro|athlete']),
                    ('健身爱好者', ['健身|训练|fitness']),
                    ('运动新手', ['入门|新手|beginner']),
                    ('户外运动者', ['户外|adventure'])
                ]
            }
        }

        # 初始化智普AI
        self.zhipuai_api_key = zhipuai_api_key or os.getenv("ZHIPUAI_API_KEY")
        self.zhipuai_client = ZhipuAI(api_key=self.zhipuai_api_key)
        self.use_ai_analysis = use_ai_analysis  # 控制是否使用AI分析模式

        # 多线程配置
        self.max_workers = max_workers
        self.thread_local = threading.local()

        # MongoDB 配置
        self.mongo_client = MongoClient(
            "mongodb://admin:password@59.110.113.0:27017/",
            authSource="admin",
            serverSelectionTimeoutMS=5000  # 5秒超时
        )
        # 强制触发一个连接测试
        self.mongo_client.admin.command('ping')
        print("✅ MongoDB 连接成功")
        self.db = self.mongo_client["ecommerce_analysis"]
        # 创建五个集合分别存储不同阶段的结果
        self.categories_collection = self.db["categories"]
        self.user_groups_collection = self.db["user_groups"]
        self.questions_collection = self.db["questions"]
        self.answers_collection = self.db["answers"]
        self.brands_collection = self.db["brands"]
        self.combined_collection = self.db["combined_results"]

    def save_to_mongodb(self, collection_name: str, url: str, data: dict):
        """将数据保存到指定的MongoDB集合"""
        document = {
            "url": url,
            "data": data,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        try:
            collection = getattr(self, f"{collection_name}_collection")
            result = collection.insert_one(document)
            print(f"数据已保存到 {collection_name} 集合，文档ID: {result.inserted_id}")
            return result.inserted_id
        except Exception as e:
            print(f"保存到 {collection_name} 集合失败: {e}")
            return None

    def _get_session(self):
        """获取线程安全的HTTP会话"""
        if not hasattr(self.thread_local, 'session'):
            self.thread_local.session = requests.Session()
        return self.thread_local.session

    def _extract_page_content(self, url: str) -> str:
        """提取网页主要文本内容（用于AI分析）"""
        try:
            session = self._get_session()
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = session.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            # 移除脚本、样式等非文本元素
            for script in soup(["script", "style"]):
                script.decompose()

            page_text = soup.get_text()
            # 清理文本格式
            page_text = re.sub(r'\n+', ' ', page_text)
            page_text = re.sub(r'\s+', ' ', page_text)
            return page_text.strip()[:10000]  # 限制长度防止超出token
        except Exception as e:
            print(f"提取页面内容出错: {e}")
            return ""

    #输入参数：url（目标网页 URL，字符串）
    #输出：包含产品类别的字典（如{"categories": ["服装", "鞋类"]}）
    def extract_categories(self, url: str) -> Dict[str, Any]:
        """提取产品大类（独立方法）"""
        if self.use_ai_analysis:
            page_content = self._extract_page_content(url)
            categories = self._analyze_categories_with_ai(page_content)
            result = {"categories": categories}
        else:
            # 规则分析
            try:
                session = self._get_session()
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = session.get(url, headers=headers, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text().lower()

                categories = []
                for cat_name, cat_data in self.category_user_mapping.items():
                    if any(re.search(p, page_text, re.IGNORECASE) for p in cat_data['patterns']):
                        categories.append(cat_name)
                result = {"categories": categories}
            except Exception as e:
                print(f"提取出错: {e}")
                result = {"categories": []}

        # 保存到MongoDB
        self.save_to_mongodb("categories", url, result)
        return result

    #输入参数：url（目标网页URL，字符串）
    #输出：按产品类别分组的用户群体字典
    def extract_user_groups(self, url: str) -> Dict[str, Any]:
        """分析目标人群（独立方法）"""
        categories_result = self.extract_categories(url)
        categories = categories_result.get("categories", [])

        if self.use_ai_analysis:
            page_content = self._extract_page_content(url)
            result = defaultdict(list)
            for category in categories:
                user_groups = self._analyze_user_groups_with_ai(page_content, category)
                if user_groups:
                    result[category] = user_groups
            result = dict(result)
        else:
            # 规则分析
            try:
                session = self._get_session()
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = session.get(url, headers=headers, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text().lower()

                result = {}
                for cat_name, cat_data in self.category_user_mapping.items():
                    if cat_name in categories:
                        users = set()
                        for user_name, keywords in cat_data['user_groups']:
                            if any(re.search(k, page_text, re.IGNORECASE) for k in keywords):
                                users.add(user_name)
                        if users:
                            result[cat_name] = sorted(users)
            except Exception as e:
                print(f"提取出错: {e}")
                result = {}

        # 保存到MongoDB
        self.save_to_mongodb("user_groups", url, result)
        return result
    #输入参数：url：目标网页 URL（字符串）questions_per_group：每组用户生成的问题数（整数，默认 3）
    #输出：按类别和用户群体分组的问题字典
    def generate_questions(self, url: str, questions_per_group: int = 3) -> Dict[str, Any]:
        """生成问题（独立方法）"""
        user_groups_result = self.extract_user_groups(url)
        if not user_groups_result:
            return {}

        result = defaultdict(dict)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_params = {}
            for category, user_groups in user_groups_result.items():
                for user_group in user_groups:
                    future = executor.submit(
                        self._generate_questions_with_ai,
                        category=category,
                        user_group=user_group,
                        brand="",
                        k=questions_per_group
                    )
                    future_to_params[future] = (category, user_group)

            for future in concurrent.futures.as_completed(future_to_params):
                category, user_group = future_to_params[future]
                try:
                    questions = future.result()
                    result[category][user_group] = questions
                except Exception as e:
                    print(f"生成问题时出错: {e}")

        # 保存到MongoDB
        self.save_to_mongodb("questions", url, dict(result))
        return dict(result)
    #输入参数：url：目标网页 URL（字符串）answers_per_question：每个问题生成的回答数（整数，默认 3）
    # 输出：按类别、用户群体、问题分组的回答字典
    def generate_answers(self, url: str, answers_per_question: int = 3) -> Dict[str, Any]:
        """生成回答（独立方法）"""
        questions_result = self.generate_questions(url)
        if not questions_result:
            return {}

        result = defaultdict(lambda: defaultdict(dict))
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_params = {}
            for category, user_data in questions_result.items():
                for user_group, questions in user_data.items():
                    for question in questions:
                        future = executor.submit(
                            self._generate_answers_with_ai,
                            question=question,
                            category=category,
                            user_group=user_group,
                            k=answers_per_question
                        )
                        future_to_params[future] = (category, user_group, question)

            for future in concurrent.futures.as_completed(future_to_params):
                category, user_group, question = future_to_params[future]
                try:
                    answers = future.result()
                    result[category][user_group][question] = answers
                except Exception as e:
                    print(f"生成回答时出错: {e}")

        # 保存到MongoDB
        self.save_to_mongodb("answers", url, dict(result))
        return dict(result)
#输入参数：url（目标网页 URL，字符串）
#输出：包含品牌信息的多层级字典（同上generate_answers输出，但增加品牌提取）
    def extract_brands(self, url: str) -> Dict[str, Any]:
        """提取品牌（独立方法）"""
        answers_result = self.generate_answers(url)
        if not answers_result:
            return {}

        result = defaultdict(lambda: defaultdict(dict))
        for category, user_data in answers_result.items():
            for user_group, qa_data in user_data.items():
                for question, answers in qa_data.items():
                    brands_list = []
                    for answer in answers:
                        brands = self._extract_brands_from_answer(answer)
                        brands_list.append({
                            "answer": answer,
                            "brands": brands
                        })
                    result[category][user_group][question] = brands_list

        # 保存到MongoDB
        self.save_to_mongodb("brands", url, dict(result))

        # 同时保存完整结果到combined_results集合
        self.save_to_mongodb("combined", url, dict(result))

        return dict(result)

    # 以下是原有的辅助方法（保持不变）
    def _analyze_categories_with_ai(self, page_content: str) -> List[str]:
        """调用AI分析网页内容，识别产品大类"""
        prompt = f"""作为电商行业专家，请分析以下网页内容，识别出其中主要的产品大类（产业线）。
        要求：
        1. 输出3-5个最主要的产品大类，使用中文通用术语（如"服装"、"鞋类"、"运动器材")
        2. 每个类别占一行，避免重复或细分品类

        网页内容摘要：
        {page_content if page_content else "无有效内容"}"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6
            )
            categories = [cat.strip() for cat in response.choices[0].message.content.split('\n') if cat.strip()]
            return categories[:5]
        except Exception as e:
            print(f"AI分析产品类别失败: {e}")
            return []

    def _analyze_user_groups_with_ai(self, page_content: str, category: str) -> List[str]:
        """针对特定产品类别，调用AI分析目标用户群体"""
        prompt = f"""作为电商行业专家，请分析以下网页中关于"{category}"产品的目标用户群体。
        要求：
        1. 输出3-5个最相关的用户群体，使用中文（如"跑步爱好者"、"青少年")
        2. 每个群体占一行，避免重复或模糊描述

        网页内容摘要：
        {page_content if page_content else "无有效内容"}"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6
            )
            user_groups = [group.strip() for group in response.choices[0].message.content.split('\n') if group.strip()]
            return user_groups[:5]
        except Exception as e:
            print(f"AI分析{category}的用户群体失败: {e}")
            return []

    def _generate_questions_with_ai(self, category: str, user_group: str, brand: str, k: int) -> List[str]:
        """调用智普AI生成问题"""
        prompt = f"""作为购物专家，请为{category}产品的{user_group}用户生成{k}个关于品牌推荐的问题。
        要求：
        1. 问题必须明确询问品牌推荐
        2. 反映该用户群体的核心需求
        3. 每个问题不超过25字

        示例（服装+时尚达人）:
        - 哪些品牌的潮流单品最值得购买？
        - 高端商务装推荐什么品牌？
        - 运动休闲风格有什么品牌推荐？"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            questions = [q.strip() for q in response.choices[0].message.content.split('\n') if q.strip()]
            return questions[:k]
        except Exception as e:
            print(f"AI生成问题失败: {e}")
            return [f"{category}类{user_group}用户相关问题（生成失败）"]

    def _generate_answers_with_ai(self, question: str, category: str, user_group: str, k: int) -> List[str]:
        """调用智普AI生成回答，并要求品牌用双引号包裹"""
        prompt = f"""作为{category}产品专家，请为{user_group}用户的问题提供{k}个专业回答，需包含具体品牌推荐。

        问题: "{question}"

        重要要求:
        1. 必须推荐2-3个实际存在的知名品牌（国际或本土品牌均可）
        2. 品牌名称必须用双引号包裹（例如 "Nike"、"李宁")
        3. 针对不同预算和需求提供多样化推荐
        4. 每个回答包含品牌名称和推荐理由
        5. 回答简洁明了，不超过60字

        示例回答格式:
        - 高端推荐"Gucci"和"Prada"，设计感强；平价选择"ZARA"，更新快款式多
        - 专业运动选"Nike"、"Adidas"；性价比考虑"李宁"、"安踏"
        - 奢侈品牌: "Louis Vuitton"; 轻奢: "Coach"; 快时尚: "UNIQLO"

        注意：品牌名称必须完整且准确，不要使用简称或缩写（例如用"Under Armour"而非"UA")"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8
            )
            answers = [a.strip() for a in response.choices[0].message.content.split('\n') if a.strip()]
            return answers[:k]
        except Exception as e:
            print(f"AI生成回答失败: {e}")
            return [f"针对{question}的回答（生成失败）"]

    def _extract_brands_from_answer(self, answer: str) -> List[str]:
        """从回答中提取双引号包裹的品牌名称"""
        brand_pattern = r'"([^"]+)"'
        brands = re.findall(brand_pattern, answer)

        cleaned_brands = []
        for brand in brands:
            clean_brand = brand.strip()
            clean_brand = re.sub(r'[^\w\s&-]', '', clean_brand)
            clean_brand = re.sub(r'\s+', ' ', clean_brand)
            if clean_brand:
                cleaned_brands.append(clean_brand)

        seen = set()
        unique_brands = []
        for brand in cleaned_brands:
            if brand not in seen:
                seen.add(brand)
                unique_brands.append(brand)
        return unique_brands

    def analyze_website(self, url: str, questions_per_group: int = 3, answers_per_question: int = 3,
                        use_ai_analysis: bool = False) -> Dict[str, Dict[str, Dict[str, List[Dict[str, List[str]]]]]]:
        """
        完整分析流程（现在调用各个独立方法）
        """
        self.use_ai_analysis = use_ai_analysis
        return self.extract_brands(url)


# 使用示例
if __name__ == "__main__":
    start_time = time.time()

    # 初始化分析器
    analyzer = EcommerceAnalyzer(zhipuai_api_key="125b8d8dfcf89504858730426ec28748.2IW4l1oHokX0hLeA", max_workers=5)

    # 输入目标网站URL
    website_url = "https://www.nike.com.cn/"
    print(f"\n正在分析网站: {website_url}")

    # 执行完整分析（会自动调用各个独立方法）
    analysis_result = analyzer.analyze_website(
        url=website_url,
        questions_per_group=3,
        answers_per_question=3
    )

    # 打印最终结果
    print("\n最终分析结果：")
    for category, user_data in analysis_result.items():
        print(f"\n【{category}】")
        for user_group, qa_data in user_data.items():
            print(f"\n目标用户: {user_group}")
            for i, (question, answers) in enumerate(qa_data.items(), 1):
                print(f"\n  问题 {i}: {question}")
                for j, answer_data in enumerate(answers, 1):
                    print(f"    回答 {j}: {answer_data['answer']}")
                    print(f"        提取品牌: {answer_data['brands']}")

    end_time = time.time()
    print(f"\n总耗时: {end_time - start_time:.2f}秒")

@dataclass
class AIResponse:
    question: str = ""
    answers: List[Dict[str, Any]] = None


class EcommerceAnalyzer:
    def __init__(self, zhipuai_api_key: str = None, max_workers: int = 10, use_ai_analysis: bool = False):
        # 产品大类与人群映射（保留规则库）
        self.category_user_mapping = {
            '服装': {
                'patterns': ['服装|服饰|衣服|clothing|apparel|wear|上衣|下装|外套|T恤|卫衣'],
                'user_groups': [
                    ('男性', ['男|男士|男性|men']),
                    ('女性', ['女|女士|女性|women']),
                    ('青少年', ['青少年|少年|teen|youth']),
                    ('儿童', ['儿童|小孩|kids|children']),
                    ('运动员', ['运动员|运动|athlete']),
                    ('时尚达人', ['时尚|潮流|fashion|trendy']),
                    ('商务人士', ['商务|正装|business|formal'])
                ]
            },
            '鞋类': {
                'patterns': ['鞋|靴|footwear|sneaker|shoes'],
                'user_groups': [
                    ('跑步爱好者', ['跑步|running']),
                    ('篮球爱好者', ['篮球|basketball']),
                    ('足球爱好者', ['足球|soccer']),
                    ('健身人士', ['健身|训练|training']),
                    ('户外爱好者', ['户外|登山|hiking|outdoor']),
                    ('时尚潮人', ['时尚|潮流|fashion|trendy']),
                    ('日常穿着者', ['休闲|日常|casual|walking'])
                ]
            },
            '运动器材': {
                'patterns': ['器材|装备|equipment|gear'],
                'user_groups': [
                    ('专业运动员', ['专业|pro|athlete']),
                    ('健身爱好者', ['健身|训练|fitness']),
                    ('运动新手', ['入门|新手|beginner']),
                    ('户外运动者', ['户外|adventure'])
                ]
            }
        }

        # 初始化智普AI
        self.zhipuai_api_key = zhipuai_api_key or os.getenv("ZHIPUAI_API_KEY")
        self.zhipuai_client = ZhipuAI(api_key=self.zhipuai_api_key)
        self.use_ai_analysis = use_ai_analysis  # 控制是否使用AI分析模式

        # 多线程配置
        self.max_workers = max_workers
        self.thread_local = threading.local()

        # MongoDB 配置
        self.mongo_client = MongoClient(
            "mongodb://admin:password@59.110.113.0:27017/",
            authSource="admin",
            serverSelectionTimeoutMS=5000  # 5秒超时
        )
        # 强制触发一个连接测试
        self.mongo_client.admin.command('ping')
        print("✅ MongoDB 连接成功")
        self.db = self.mongo_client["ecommerce_analysis"]
        # 创建五个集合分别存储不同阶段的结果
        self.categories_collection = self.db["categories"]
        self.user_groups_collection = self.db["user_groups"]
        self.questions_collection = self.db["questions"]
        self.answers_collection = self.db["answers"]
        self.brands_collection = self.db["brands"]
        self.combined_collection = self.db["combined_results"]

    def save_to_mongodb(self, collection_name: str, url: str, data: dict):
        """将数据保存到指定的MongoDB集合"""
        document = {
            "url": url,
            "data": data,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        try:
            collection = getattr(self, f"{collection_name}_collection")
            result = collection.insert_one(document)
            print(f"数据已保存到 {collection_name} 集合，文档ID: {result.inserted_id}")
            return result.inserted_id
        except Exception as e:
            print(f"保存到 {collection_name} 集合失败: {e}")
            return None

    def _get_session(self):
        """获取线程安全的HTTP会话"""
        if not hasattr(self.thread_local, 'session'):
            self.thread_local.session = requests.Session()
        return self.thread_local.session

    def _extract_page_content(self, url: str) -> str:
        """提取网页主要文本内容（用于AI分析）"""
        try:
            session = self._get_session()
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = session.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            # 移除脚本、样式等非文本元素
            for script in soup(["script", "style"]):
                script.decompose()

            page_text = soup.get_text()
            # 清理文本格式
            page_text = re.sub(r'\n+', ' ', page_text)
            page_text = re.sub(r'\s+', ' ', page_text)
            return page_text.strip()[:10000]  # 限制长度防止超出token
        except Exception as e:
            print(f"提取页面内容出错: {e}")
            return ""

    #输入参数：url（目标网页 URL，字符串）
    #输出：包含产品类别的字典（如{"categories": ["服装", "鞋类"]}）
    def extract_categories(self, url: str) -> Dict[str, Any]:
        """提取产品大类（独立方法）"""
        if self.use_ai_analysis:
            page_content = self._extract_page_content(url)
            categories = self._analyze_categories_with_ai(page_content)
            result = {"categories": categories}
        else:
            # 规则分析
            try:
                session = self._get_session()
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = session.get(url, headers=headers, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text().lower()

                categories = []
                for cat_name, cat_data in self.category_user_mapping.items():
                    if any(re.search(p, page_text, re.IGNORECASE) for p in cat_data['patterns']):
                        categories.append(cat_name)
                result = {"categories": categories}
            except Exception as e:
                print(f"提取出错: {e}")
                result = {"categories": []}

        # 保存到MongoDB
        self.save_to_mongodb("categories", url, result)
        return result

    #输入参数：url（目标网页URL，字符串）
    #输出：按产品类别分组的用户群体字典
    def extract_user_groups(self, url: str) -> Dict[str, Any]:
        """分析目标人群（独立方法）"""
        categories_result = self.extract_categories(url)
        categories = categories_result.get("categories", [])

        if self.use_ai_analysis:
            page_content = self._extract_page_content(url)
            result = defaultdict(list)
            for category in categories:
                user_groups = self._analyze_user_groups_with_ai(page_content, category)
                if user_groups:
                    result[category] = user_groups
            result = dict(result)
        else:
            # 规则分析
            try:
                session = self._get_session()
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = session.get(url, headers=headers, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text().lower()

                result = {}
                for cat_name, cat_data in self.category_user_mapping.items():
                    if cat_name in categories:
                        users = set()
                        for user_name, keywords in cat_data['user_groups']:
                            if any(re.search(k, page_text, re.IGNORECASE) for k in keywords):
                                users.add(user_name)
                        if users:
                            result[cat_name] = sorted(users)
            except Exception as e:
                print(f"提取出错: {e}")
                result = {}

        # 保存到MongoDB
        self.save_to_mongodb("user_groups", url, result)
        return result
    #输入参数：url：目标网页 URL（字符串）questions_per_group：每组用户生成的问题数（整数，默认 3）
    #输出：按类别和用户群体分组的问题字典
    def generate_questions(self, url: str, questions_per_group: int = 3) -> Dict[str, Any]:
        """生成问题（独立方法）"""
        user_groups_result = self.extract_user_groups(url)
        if not user_groups_result:
            return {}

        result = defaultdict(dict)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_params = {}
            for category, user_groups in user_groups_result.items():
                for user_group in user_groups:
                    future = executor.submit(
                        self._generate_questions_with_ai,
                        category=category,
                        user_group=user_group,
                        brand="",
                        k=questions_per_group
                    )
                    future_to_params[future] = (category, user_group)

            for future in concurrent.futures.as_completed(future_to_params):
                category, user_group = future_to_params[future]
                try:
                    questions = future.result()
                    result[category][user_group] = questions
                except Exception as e:
                    print(f"生成问题时出错: {e}")

        # 保存到MongoDB
        self.save_to_mongodb("questions", url, dict(result))
        return dict(result)
    #输入参数：url：目标网页 URL（字符串）answers_per_question：每个问题生成的回答数（整数，默认 3）
    # 输出：按类别、用户群体、问题分组的回答字典
    def generate_answers(self, url: str, answers_per_question: int = 3) -> Dict[str, Any]:
        """生成回答（独立方法）"""
        questions_result = self.generate_questions(url)
        if not questions_result:
            return {}

        result = defaultdict(lambda: defaultdict(dict))
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_params = {}
            for category, user_data in questions_result.items():
                for user_group, questions in user_data.items():
                    for question in questions:
                        future = executor.submit(
                            self._generate_answers_with_ai,
                            question=question,
                            category=category,
                            user_group=user_group,
                            k=answers_per_question
                        )
                        future_to_params[future] = (category, user_group, question)

            for future in concurrent.futures.as_completed(future_to_params):
                category, user_group, question = future_to_params[future]
                try:
                    answers = future.result()
                    result[category][user_group][question] = answers
                except Exception as e:
                    print(f"生成回答时出错: {e}")

        # 保存到MongoDB
        self.save_to_mongodb("answers", url, dict(result))
        return dict(result)
#输入参数：url（目标网页 URL，字符串）
#输出：包含品牌信息的多层级字典（同上generate_answers输出，但增加品牌提取）
    def extract_brands(self, url: str) -> Dict[str, Any]:
        """提取品牌（独立方法）"""
        answers_result = self.generate_answers(url)
        if not answers_result:
            return {}

        result = defaultdict(lambda: defaultdict(dict))
        for category, user_data in answers_result.items():
            for user_group, qa_data in user_data.items():
                for question, answers in qa_data.items():
                    brands_list = []
                    for answer in answers:
                        brands = self._extract_brands_from_answer(answer)
                        brands_list.append({
                            "answer": answer,
                            "brands": brands
                        })
                    result[category][user_group][question] = brands_list

        # 保存到MongoDB
        self.save_to_mongodb("brands", url, dict(result))

        # 同时保存完整结果到combined_results集合
        self.save_to_mongodb("combined", url, dict(result))

        return dict(result)

    # 以下是原有的辅助方法（保持不变）
    def _analyze_categories_with_ai(self, page_content: str) -> List[str]:
        """调用AI分析网页内容，识别产品大类"""
        prompt = f"""作为电商行业专家，请分析以下网页内容，识别出其中主要的产品大类（产业线）。
        要求：
        1. 输出3-5个最主要的产品大类，使用中文通用术语（如"服装"、"鞋类"、"运动器材")
        2. 每个类别占一行，避免重复或细分品类

        网页内容摘要：
        {page_content if page_content else "无有效内容"}"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6
            )
            categories = [cat.strip() for cat in response.choices[0].message.content.split('\n') if cat.strip()]
            return categories[:5]
        except Exception as e:
            print(f"AI分析产品类别失败: {e}")
            return []

    def _analyze_user_groups_with_ai(self, page_content: str, category: str) -> List[str]:
        """针对特定产品类别，调用AI分析目标用户群体"""
        prompt = f"""作为电商行业专家，请分析以下网页中关于"{category}"产品的目标用户群体。
        要求：
        1. 输出3-5个最相关的用户群体，使用中文（如"跑步爱好者"、"青少年")
        2. 每个群体占一行，避免重复或模糊描述

        网页内容摘要：
        {page_content if page_content else "无有效内容"}"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6
            )
            user_groups = [group.strip() for group in response.choices[0].message.content.split('\n') if group.strip()]
            return user_groups[:5]
        except Exception as e:
            print(f"AI分析{category}的用户群体失败: {e}")
            return []

    def _generate_questions_with_ai(self, category: str, user_group: str, brand: str, k: int) -> List[str]:
        """调用智普AI生成问题"""
        prompt = f"""作为购物专家，请为{category}产品的{user_group}用户生成{k}个关于品牌推荐的问题。
        要求：
        1. 问题必须明确询问品牌推荐
        2. 反映该用户群体的核心需求
        3. 每个问题不超过25字

        示例（服装+时尚达人）:
        - 哪些品牌的潮流单品最值得购买？
        - 高端商务装推荐什么品牌？
        - 运动休闲风格有什么品牌推荐？"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            questions = [q.strip() for q in response.choices[0].message.content.split('\n') if q.strip()]
            return questions[:k]
        except Exception as e:
            print(f"AI生成问题失败: {e}")
            return [f"{category}类{user_group}用户相关问题（生成失败）"]

    def _generate_answers_with_ai(self, question: str, category: str, user_group: str, k: int) -> List[str]:
        """调用智普AI生成回答，并要求品牌用双引号包裹"""
        prompt = f"""作为{category}产品专家，请为{user_group}用户的问题提供{k}个专业回答，需包含具体品牌推荐。

        问题: "{question}"

        重要要求:
        1. 必须推荐2-3个实际存在的知名品牌（国际或本土品牌均可）
        2. 品牌名称必须用双引号包裹（例如 "Nike"、"李宁")
        3. 针对不同预算和需求提供多样化推荐
        4. 每个回答包含品牌名称和推荐理由
        5. 回答简洁明了，不超过60字

        示例回答格式:
        - 高端推荐"Gucci"和"Prada"，设计感强；平价选择"ZARA"，更新快款式多
        - 专业运动选"Nike"、"Adidas"；性价比考虑"李宁"、"安踏"
        - 奢侈品牌: "Louis Vuitton"; 轻奢: "Coach"; 快时尚: "UNIQLO"

        注意：品牌名称必须完整且准确，不要使用简称或缩写（例如用"Under Armour"而非"UA")"""

        try:
            response = self.zhipuai_client.chat.completions.create(
                model="glm-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8
            )
            answers = [a.strip() for a in response.choices[0].message.content.split('\n') if a.strip()]
            return answers[:k]
        except Exception as e:
            print(f"AI生成回答失败: {e}")
            return [f"针对{question}的回答（生成失败）"]

    def _extract_brands_from_answer(self, answer: str) -> List[str]:
        """从回答中提取双引号包裹的品牌名称"""
        brand_pattern = r'"([^"]+)"'
        brands = re.findall(brand_pattern, answer)

        cleaned_brands = []
        for brand in brands:
            clean_brand = brand.strip()
            clean_brand = re.sub(r'[^\w\s&-]', '', clean_brand)
            clean_brand = re.sub(r'\s+', ' ', clean_brand)
            if clean_brand:
                cleaned_brands.append(clean_brand)

        seen = set()
        unique_brands = []
        for brand in cleaned_brands:
            if brand not in seen:
                seen.add(brand)
                unique_brands.append(brand)
        return unique_brands

    def analyze_website(self, url: str, questions_per_group: int = 3, answers_per_question: int = 3,
                        use_ai_analysis: bool = False) -> Dict[str, Dict[str, Dict[str, List[Dict[str, List[str]]]]]]:
        """
        完整分析流程（现在调用各个独立方法）
        """
        self.use_ai_analysis = use_ai_analysis
        return self.extract_brands(url)


# 使用示例
if __name__ == "__main__":
    start_time = time.time()

    # 初始化分析器
    analyzer = EcommerceAnalyzer(zhipuai_api_key="125b8d8dfcf89504858730426ec28748.2IW4l1oHokX0hLeA", max_workers=5)

    # 输入目标网站URL
    website_url = "https://www.nike.com.cn/"
    print(f"\n正在分析网站: {website_url}")

    # 执行完整分析（会自动调用各个独立方法）
    analysis_result = analyzer.analyze_website(
        url=website_url,
        questions_per_group=3,
        answers_per_question=3
    )

    # 打印最终结果
    print("\n最终分析结果：")
    for category, user_data in analysis_result.items():
        print(f"\n【{category}】")
        for user_group, qa_data in user_data.items():
            print(f"\n目标用户: {user_group}")
            for i, (question, answers) in enumerate(qa_data.items(), 1):
                print(f"\n  问题 {i}: {question}")
                for j, answer_data in enumerate(answers, 1):
                    print(f"    回答 {j}: {answer_data['answer']}")
                    print(f"        提取品牌: {answer_data['brands']}")

    end_time = time.time()
    print(f"\n总耗时: {end_time - start_time:.2f}秒")