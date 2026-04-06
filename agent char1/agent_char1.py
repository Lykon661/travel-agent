AGENT_SYSTEM_PROMPT = """
你是一个智能旅行助手。你的任务是分析用户的请求，并使用可用工具一步步地解决问题。

# 可用工具:
- `get_weather(city: str)`: 查询指定城市的实时天气。
- `get_attraction(city: str, weather: str)`: 根据城市和天气搜索多个候选景点，并检查这一批景点的票务状态。
- `check_ticket_availability(attraction: str)`: 检查景点门票是否售罄。
- `get_backup_attraction(city: str, weather: str, excluded: str)`: 当首选景点不可用时，搜索并检查备选景点的票务状态。

# 输出格式要求:
你的每次回复必须严格遵循以下格式，包含一对Thought和Action：

Thought: [你的思考过程和下一步计划]
Action: [你要执行的具体行动]

Action的格式必须是以下之一：
1. 调用工具：function_name(arg_name="arg_value")
2. 结束任务：Finish[最终答案]

# 重要提示:
- 每次只输出一对Thought-Action
- Action必须在同一行，不要换行
- `get_attraction` 和 `get_backup_attraction` 会自动检查这一批候选景点的票务状态
- 如果推荐的景点已售罄，请继续调用备选景点工具，不要直接结束
- 如果用户连续拒绝了 3 次推荐，请根据用户记忆调整推荐策略，再继续推荐
- 当收集到足够信息可以回答用户问题时，必须使用 Action: Finish[最终答案] 格式结束

请开始吧！
"""

import requests
import random

user_memory = {
    "city": None,
    "likes": [],
    "dislikes": [],
    "budget": None,
}

session_state = {
    "rejection_count": 0,
    "strategy": "默认策略",
}

def extract_city(user_text: str):
    """从常见表达里提取城市名。"""
    patterns = [
        r"(?:去|到|在)([\u4e00-\u9fa5]{2,8})(?:旅游|旅行|玩|逛|看景点|看风景)?",
        r"([\u4e00-\u9fa5]{2,8})(?:的天气|天气|景点|旅游|旅行)",
    ]
    invalid_words = {"今天", "明天", "周末", "预算", "便宜", "贵一点", "历史文化", "自然风景"}

    for pattern in patterns:
        match = re.search(pattern, user_text)
        if match:
            city = match.group(1)
            if city not in invalid_words:
                return city
    return None

def update_memory(user_text: str):
    """从用户输入中提取基础偏好和常见反馈。"""
    city = extract_city(user_text)
    if city:
        user_memory["city"] = city

    if "历史" in user_text or "文化" in user_text:
        if "历史文化" not in user_memory["likes"]:
            user_memory["likes"].append("历史文化")

    if "自然" in user_text or "风景" in user_text:
        if "自然风景" not in user_memory["likes"]:
            user_memory["likes"].append("自然风景")

    if "拍照" in user_text:
        if "适合拍照" not in user_memory["likes"]:
            user_memory["likes"].append("适合拍照")

    if "便宜" in user_text or "预算低" in user_text or "不要太贵" in user_text:
        user_memory["budget"] = "低"
    elif "预算高" in user_text or "贵一点也可以" in user_text:
        user_memory["budget"] = "高"

    if "不喜欢博物馆" in user_text:
        if "博物馆" not in user_memory["dislikes"]:
            user_memory["dislikes"].append("博物馆")

    if "不要太远" in user_text or "别太远" in user_text:
        if "太远" not in user_memory["dislikes"]:
            user_memory["dislikes"].append("太远")

    if "不喜欢人多" in user_text or "别太拥挤" in user_text:
        if "人多拥挤" not in user_memory["dislikes"]:
            user_memory["dislikes"].append("人多拥挤")

def update_rejection_state(user_text: str):
    """识别用户是否在拒绝推荐，并在连续拒绝时调整策略。"""
    rejection_keywords = ["不喜欢", "不要这个", "换一个", "换个", "还有别的吗", "不想去", "不行"]
    acceptance_keywords = ["可以", "不错", "就这个", "喜欢这个", "可以去"]

    if any(keyword in user_text for keyword in rejection_keywords):
        session_state["rejection_count"] += 1
    elif any(keyword in user_text for keyword in acceptance_keywords):
        session_state["rejection_count"] = 0
        session_state["strategy"] = "默认策略"
        return
    else:
        return

    if session_state["rejection_count"] >= 3:
        strategy_parts = []
        if user_memory["budget"] == "低":
            strategy_parts.append("优先低预算景点")
        if user_memory["likes"]:
            strategy_parts.append(f"优先考虑{ '、'.join(user_memory['likes']) }")
        if user_memory["dislikes"]:
            strategy_parts.append(f"避开{ '、'.join(user_memory['dislikes']) }")
        if not strategy_parts:
            strategy_parts.append("改为推荐更通用、更容易接受的热门景点")

        session_state["strategy"] = "；".join(strategy_parts)

def build_memory_summary() -> str:
    city_text = user_memory["city"] or "未知"
    likes_text = "、".join(user_memory["likes"]) if user_memory["likes"] else "未知"
    dislikes_text = "、".join(user_memory["dislikes"]) if user_memory["dislikes"] else "未知"
    budget_text = user_memory["budget"] or "未知"
    strategy_text = session_state["strategy"]
    rejection_count_text = session_state["rejection_count"]
    return (
        f"用户偏好记忆:\n"
        f"- 城市: {city_text}\n"
        f"- 喜欢: {likes_text}\n"
        f"- 不喜欢: {dislikes_text}\n"
        f"- 预算: {budget_text}\n"
        f"- 连续拒绝次数: {rejection_count_text}\n"
        f"- 当前推荐策略: {strategy_text}"
    )

def get_weather(city: str) -> str:
    """
    通过调用 wttr.in API 查询真实的天气信息。
    """
    # API端点，我们请求JSON格式的数据
    url = f"https://wttr.in/{city}?format=j1"
    
    try:
        # 发起网络请求
        response = requests.get(url)
        # 检查响应状态码是否为200 (成功)
        response.raise_for_status() 
        # 解析返回的JSON数据
        data = response.json()
        
        # 提取当前天气状况
        current_condition = data['current_condition'][0]
        weather_desc = current_condition['weatherDesc'][0]['value']
        temp_c = current_condition['temp_C']
        
        # 格式化成自然语言返回
        return f"{city}当前天气:{weather_desc}，气温{temp_c}摄氏度"
        
    except requests.exceptions.RequestException as e:
        # 处理网络错误
        return f"错误:查询天气时遇到网络问题 - {e}"
    except (KeyError, IndexError) as e:
        # 处理数据解析错误
        return f"错误:解析天气数据失败，可能是城市名称无效 - {e}"

import os
from tavily import TavilyClient

def extract_attraction_name(title: str) -> str:
    """从搜索结果标题中提取较短的景点名。"""
    for separator in [" - ", "｜", "|", "—", "_", "(", "（", "：", ":"]:
        if separator in title:
            return title.split(separator)[0].strip()
    return title.strip()

def build_checked_attraction_result(results, excluded_names=None) -> str:
    """检查一批候选景点的票务状态，并返回可预约景点。"""
    excluded_names = excluded_names or set()
    checked = []
    available = []

    for result in results:
        attraction_name = extract_attraction_name(result["title"])
        if not attraction_name or attraction_name in excluded_names:
            continue

        ticket_status = check_ticket_availability(attraction_name)
        checked.append(f"{attraction_name}: {ticket_status}")
        if "可预约" in ticket_status:
            available.append(
                f"- {attraction_name}: {result['content']}\n  票务状态: 可预约"
            )

    if available:
        return "已检查本轮候选景点票务，以下景点当前可预约:\n" + "\n".join(available)

    if checked:
        return "本轮候选景点已全部检查，但暂时都不可预约:\n" + "\n".join(f"- {item}" for item in checked)

    return "抱歉，没有找到可供检查票务状态的候选景点。"

def get_attraction(city: str, weather: str) -> str:
    """
    根据城市和天气，使用Tavily Search API搜索并返回优化后的景点推荐。
    """
    # 1. 从环境变量中读取API密钥
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "错误:未配置TAVILY_API_KEY环境变量。"

    # 2. 初始化Tavily客户端
    tavily = TavilyClient(api_key=api_key)
    
    # 3. 构造一个精确的查询
    query = f"'{city}' 在'{weather}'天气下最值得去的旅游景点推荐及理由"
    
    try:
        # 4. 调用API，include_answer=True会返回一个综合性的回答
        response = tavily.search(query=query, search_depth="basic", include_answer=True)
        
        results = response.get("results", [])
        if not results:
             return "抱歉，没有找到相关的旅游景点推荐。"

        return build_checked_attraction_result(results)

    except Exception as e:
        return f"错误:执行Tavily搜索时出现问题 - {e}"

def check_ticket_availability(attraction: str) -> str:
    """基础版票务检查：随机模拟 1/4 概率售罄。"""
    if random.random() < 0.25:
        return f"{attraction}门票已售罄，请推荐其他景点。"
    return f"{attraction}当前可预约，可以正常推荐。"

def get_backup_attraction(city: str, weather: str, excluded: str) -> str:
    """当首选景点不可用时，排除指定景点后给出备选推荐。"""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "错误:未配置TAVILY_API_KEY环境变量。"

    tavily = TavilyClient(api_key=api_key)
    query = f"'{city}' 在'{weather}'天气下，除了'{excluded}'之外，还有哪些值得推荐的旅游景点及理由"

    try:
        response = tavily.search(query=query, search_depth="basic", include_answer=True)
        results = response.get("results", [])
        if not results:
             return f"抱歉，暂时没有找到除{excluded}之外合适的备选景点。"

        excluded_names = {name.strip() for name in excluded.split("|") if name.strip()}
        return build_checked_attraction_result(results, excluded_names=excluded_names)

    except Exception as e:
        return f"错误:执行备选景点搜索时出现问题 - {e}"

    # 将所有工具函数放入一个字典，方便后续调用
available_tools ={
    "get_weather": get_weather,
    "get_attraction": get_attraction,
    "check_ticket_availability": check_ticket_availability,
    "get_backup_attraction": get_backup_attraction,
}

from openai import OpenAI

class OpenAICompatibleClient:
    """
    一个用于调用任何兼容OpenAI接口的LLM服务的客户端。
    """
    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, prompt: str, system_prompt: str) -> str:
        """调用LLM API来生成回应。"""
        print("正在调用大语言模型...")
        try:
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': prompt}
            ]
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False
            )
            answer = response.choices[0].message.content
            print("大语言模型响应成功。")
            return answer
        except Exception as e:
            print(f"调用LLM API时发生错误: {e}")
            return "错误:调用语言模型服务时出错。"

import re

# --- 1. 配置LLM客户端 ---
# 请通过环境变量配置凭证，避免将密钥写入源码
API_KEY = os.environ.get("MINIMAX_API_KEY")
BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
MODEL_ID = os.environ.get("MINIMAX_MODEL_ID", "MiniMax-M2.7")

if not API_KEY:
    raise RuntimeError("未配置 MINIMAX_API_KEY 环境变量。")


llm = OpenAICompatibleClient(
    model=MODEL_ID,
    api_key=API_KEY,
    base_url=BASE_URL
)

# --- 2. 进入交互会话 ---
print("请输入你的旅行需求，输入 exit 结束对话。")

while True:
    user_prompt = input("你: ").strip()
    if user_prompt.lower() in {"exit", "quit"}:
        print("对话结束。")
        break
    if not user_prompt:
        print("请输入有效的旅行需求。")
        continue

    update_memory(user_prompt)
    update_rejection_state(user_prompt)
    prompt_history = [f"用户请求: {user_prompt}"]

    print(f"用户输入: {user_prompt}\n" + "="*40)

    # --- 3. 运行主循环 ---
    for i in range(5): # 设置最大循环次数
        print(f"--- 循环 {i+1} ---\n")
        
        # 3.1. 构建Prompt
        memory_info = build_memory_summary()
        full_prompt = memory_info + "\n\n" + "\n".join(prompt_history)
        
        # 3.2. 调用LLM进行思考
        llm_output = llm.generate(full_prompt, system_prompt=AGENT_SYSTEM_PROMPT)
        # 模型可能会输出多余的Thought-Action，需要截断
        match = re.search(r'(Thought:.*?Action:.*?)(?=\n\s*(?:Thought:|Action:|Observation:)|\Z)', llm_output, re.DOTALL)
        if match:
            truncated = match.group(1).strip()
            if truncated != llm_output.strip():
                llm_output = truncated
                print("已截断多余的 Thought-Action 对")
        print(f"模型输出:\n{llm_output}\n")
        prompt_history.append(llm_output)
        
        # 3.3. 解析并执行行动
        action_match = re.search(r"Action: (.*)", llm_output, re.DOTALL)
        if not action_match:
            observation = "错误: 未能解析到 Action 字段。请确保你的回复严格遵循 'Thought: ... Action: ...' 的格式。"
            observation_str = f"Observation: {observation}"
            print(f"{observation_str}\n" + "="*40)
            prompt_history.append(observation_str)
            continue
        action_str = action_match.group(1).strip()

        if action_str.startswith("Finish"):
            finish_match = re.match(r"Finish\s*\[(.*)\]\s*$", action_str, re.DOTALL)
            if not finish_match:
                observation = f"错误: Finish 格式不正确。请严格使用 Finish[最终答案]。当前 Action 为: {action_str}"
                observation_str = f"Observation: {observation}"
                print(f"{observation_str}\n" + "="*40)
                prompt_history.append(observation_str)
                continue

            final_answer = finish_match.group(1).strip()
            print(f"任务完成，最终答案: {final_answer}")
            break
        
        tool_match = re.search(r"(\w+)\(", action_str)
        args_match = re.search(r"\((.*)\)", action_str)
        if not tool_match or not args_match:
            observation = f"错误: 工具调用格式不正确。请严格使用 function_name(arg_name=\"arg_value\")。当前 Action 为: {action_str}"
            observation_str = f"Observation: {observation}"
            print(f"{observation_str}\n" + "="*40)
            prompt_history.append(observation_str)
            continue

        tool_name = tool_match.group(1)
        args_str = args_match.group(1)
        kwargs = dict(re.findall(r'(\w+)="([^"]*)"', args_str))

        if tool_name in available_tools:
            observation = available_tools[tool_name](**kwargs)
        else:
            observation = f"错误:未定义的工具 '{tool_name}'"

        # 3.4. 记录观察结果
        observation_str = f"Observation: {observation}"
        print(f"{observation_str}\n" + "="*40)
        prompt_history.append(observation_str)
