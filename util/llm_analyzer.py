import json
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class LLMAnalyzer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get('llm_config', {})
        self.api_base = self.config.get('api_base')
        self.api_key = self.config.get('api_key')
        self.model = self.config.get('model')
        self.enable_sentiment = self.config.get('enable_sentiment_analysis', True)
        self.enable_summary = self.config.get('enable_summary', True)
        self.enable_anomaly = self.config.get('enable_anomaly_detection', True)
        
        # 添加初始化日志
        logger.info("LLM分析器初始化完成")
        logger.info(f"API基础URL: {self.api_base}")
        logger.info(f"模型: {self.model}")
        logger.info(f"功能状态 - 情感分析: {self.enable_sentiment}, 摘要生成: {self.enable_summary}, 异常检测: {self.enable_anomaly}")
        
    def _call_llm_api(self, prompt: str) -> Optional[str]:
        """调用 LLM API"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.config.get('max_tokens', 1000),
                "temperature": self.config.get('temperature', 0.7)
            }
            
            response = requests.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                logger.error(f"LLM API 调用失败: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"调用 LLM API 时发生错误: {str(e)}")
            return None

    def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """情感分析"""
        if not self.enable_sentiment:
            return {"sentiment": "disabled"}
            
        prompt = f"""请分析以下微博内容的情感倾向，只返回一个词（积极/中性/消极）：
        {text}"""
        
        result = self._call_llm_api(prompt)
        return {"sentiment": result.strip() if result else "unknown"}

    def generate_summary(self, text: str) -> Dict[str, Any]:
        """生成摘要"""
        if not self.enable_summary:
            return {"summary": "disabled"}
            
        prompt = f"""请为以下微博内容生成一个简短的摘要（不超过50字）：
        {text}"""
        
        result = self._call_llm_api(prompt)
        return {"summary": result.strip() if result else ""}

    def detect_anomaly(self, text: str) -> Dict[str, Any]:
        """异常检测"""
        if not self.enable_anomaly:
            return {"anomaly": "disabled"}
            
        prompt = f"""请分析以下微博内容是否存在异常（如谣言、广告或敏感信息），
        只返回一个词（正常/异常），如果异常请说明原因：
        {text}"""
        
        result = self._call_llm_api(prompt)
        return {"anomaly": result.strip() if result else "unknown"}

    def analyze_weibo(self, weibo_data: Dict[str, Any]) -> Dict[str, Any]:
        """综合分析微博内容"""
        text = weibo_data.get('text', '')
        if not text:
            return weibo_data
            
        analysis_results = {}
        
        # 情感分析
        sentiment_result = self.analyze_sentiment(text)
        analysis_results.update(sentiment_result)
        
        # 生成摘要
        summary_result = self.generate_summary(text)
        analysis_results.update(summary_result)
        
        # 异常检测
        anomaly_result = self.detect_anomaly(text)
        analysis_results.update(anomaly_result)
        
        # 将分析结果添加到原始数据中
        weibo_data['llm_analysis'] = analysis_results
        return weibo_data 