"""
core/llm_client.py

定義 LLM 客戶端的共同介面，以及呼叫不同供應商 API 的實作。
不管底層實際呼叫哪一家的 API，對外都只有一個方法：complete(prompt) -> str。

切換 LLM 供應商：
只要改動檔案最下面 get_llm_client() 這個函式，回傳你想用的 Client 即可，
其他地方（core/llm_enhancer.py）完全不用改。
"""

import os
from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    """所有 LLM 客戶端都要實作這個介面"""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """傳入一段 prompt 文字，回傳 LLM 生成的回應文字"""
        raise NotImplementedError


class AnthropicLLMClient(BaseLLMClient):
    """
    呼叫 Anthropic Claude API。

    需要先 `pip install anthropic`，並設定環境變數 ANTHROPIC_API_KEY
    （或是在建構子直接傳入 api_key）。
    """

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "找不到 Anthropic API key，請設定環境變數 ANTHROPIC_API_KEY"
            )

    def complete(self, prompt: str) -> str:
        # 延遲在這裡才 import，這樣沒有要用 LLM 功能的人，
        # 就算沒裝 anthropic 這個套件，其他功能也不會壞掉。
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class GeminiLLMClient(BaseLLMClient):
    """
    呼叫 Google Gemini API（先卡位，尚未實作內容）。

    之後要實作時，大致會長這樣：
        pip install google-generativeai

        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model)
        response = model.generate_content(prompt)
        return response.text
    """

    def __init__(self, model: str = "antigravity", api_key: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
                    raise RuntimeError(
                        "找不到 Gemini API key，請設定環境變數 GEMINI_API_KEY"
                    )

    def complete(self, prompt: str) -> str:
        # 延遲在這裡才 import，這樣沒有要用 LLM 功能的人，
        # 就算沒裝 google-genai 這個套件，其他功能也不會壞掉。
        from google import genai
        from google.genai import types

        client = genai.Client()
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,  # 控制創意度 (0.0 到 2.0 之間)
            )
        )
        
        return response.text


# ============================================================
# 切換 LLM 供應商：改這裡就好，其他地方完全不用動
# ============================================================
def get_llm_client() -> BaseLLMClient:
    #return AnthropicLLMClient()
    return GeminiLLMClient()
