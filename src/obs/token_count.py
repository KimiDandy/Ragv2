"""
Fallback token counter menggunakan tiktoken untuk estimasi lokal.
Digunakan ketika tidak bisa mendapatkan token count dari API response.
"""

import tiktoken
from typing import Optional
from loguru import logger


class TokenCounter:
    """Token counter fallback menggunakan tiktoken."""
    
    def __init__(self, encoding_name: str = "o200k_base"):
        """
        Initialize dengan encoding tertentu.
        o200k_base cocok untuk GPT-4o, GPT-4 Turbo, dan text-embedding-3-*
        """
        try:
            self.encoding = tiktoken.get_encoding(encoding_name)
            self.encoding_name = encoding_name
        except Exception as e:
            logger.warning(f"Failed to load encoding {encoding_name}: {e}. Using cl100k_base as fallback.")
            self.encoding = tiktoken.get_encoding("cl100k_base")
            self.encoding_name = "cl100k_base"
    
    def count_tokens(self, text: str) -> int:
        """Hitung jumlah token dalam text."""
        if not text:
            return 0
        
        try:
            return len(self.encoding.encode(text))
        except Exception as e:
            logger.error(f"Failed to count tokens: {e}")
            # Fallback: rough estimation (4 chars per token average)
            return max(1, len(text) // 4)
    
    def count_messages_tokens(self, messages: list, model: str = "gpt-4") -> int:
        """
        Hitung token untuk format messages OpenAI.
        Implementasi sederhana - tidak akurat 100% tapi cukup untuk estimasi.
        """
        try:
            total_tokens = 0
            
            # Token overhead per message (rough estimate)
            tokens_per_message = 3
            tokens_per_name = 1
            
            for message in messages:
                total_tokens += tokens_per_message
                
                for key, value in message.items():
                    total_tokens += self.count_tokens(str(value))
                    if key == "name":
                        total_tokens += tokens_per_name
            
            # Overhead untuk conversation
            total_tokens += 3
            
            return total_tokens
            
        except Exception as e:
            logger.error(f"Failed to count message tokens: {e}")
            # Fallback: count all text content
            total_text = ""
            for msg in messages:
                for value in msg.values():
                    total_text += str(value) + " "
            return self.count_tokens(total_text)


# Global instance
_counter: Optional[TokenCounter] = None


def get_token_counter() -> TokenCounter:
    """Get global token counter instance."""
    global _counter
    if _counter is None:
        _counter = TokenCounter()
    return _counter


def count_tokens(text: str) -> int:
    """Convenience function untuk count token."""
    return get_token_counter().count_tokens(text)


def count_messages_tokens(messages: list, model: str = "gpt-4") -> int:
    """Convenience function untuk count message tokens."""
    return get_token_counter().count_messages_tokens(messages, model)
