"""
Token Ledger System untuk tracking penggunaan token dan biaya LLM/Embeddings.
Menyimpan event ke JSONL dan membuat ringkasan harian.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Literal, Optional
from dataclasses import dataclass, asdict
from loguru import logger


@dataclass
class TokenEvent:
    """Event penggunaan token untuk satu operasi LLM/Embeddings."""
    ts: float
    step: Literal["enhancement", "embed", "chat", "planning"]
    model: str
    input_tokens: int
    output_tokens: int
    meta: Dict[str, Any]


class TokenLedger:
    """
    Sistem pelacakan penggunaan token dan biaya.
    Menyimpan event ke file JSONL dan membuat ringkasan harian.
    """
    
    def __init__(self, artefacts_dir: str = "artefacts", prices: Optional[Dict[str, float]] = None):
        self.artefacts_dir = Path(artefacts_dir)
        self.artefacts_dir.mkdir(exist_ok=True)
        
        # Harga default (USD per 1M tokens) - bisa di-override via ENV
        self.prices = prices or {
            "gpt-4.1_input": float(os.getenv("PRICE_GPT41_IN", "3.00")),
            "gpt-4.1_output": float(os.getenv("PRICE_GPT41_OUT", "12.00")),
            "gpt-4o_input": float(os.getenv("PRICE_GPT4O_IN", "2.50")),
            "gpt-4o_output": float(os.getenv("PRICE_GPT4O_OUT", "10.00")),
            "gpt-4o-mini_input": float(os.getenv("PRICE_GPT4O_MINI_IN", "0.15")),
            "gpt-4o-mini_output": float(os.getenv("PRICE_GPT4O_MINI_OUT", "0.60")),
            "text-embedding-3-large_input": float(os.getenv("PRICE_EMB_LARGE_IN", "0.13")),
            "text-embedding-3-small_input": float(os.getenv("PRICE_EMB_SMALL_IN", "0.02")),
            "text-embedding-ada-002_input": float(os.getenv("PRICE_EMB_ADA_IN", "0.10")),
        }
        
        self.jsonl_path = self.artefacts_dir / "token_usage.jsonl"
        self.summary_path = self.artefacts_dir / "token_usage_summary.md"
    
    def add(self, event: TokenEvent) -> None:
        """
        Tambahkan event baru ke ledger.
        Append ke JSONL dan update ringkasan.
        """
        try:
            # Append ke JSONL
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event)) + "\n")
            
            # Update ringkasan
            self._update_summary()
            
            logger.debug(f"Token event added: {event.step} {event.model} - {event.input_tokens}+{event.output_tokens} tokens")
            
        except Exception as e:
            logger.error(f"Failed to add token event: {e}")
    
    def add_simple(
        self,
        step: Literal["enhancement", "embed", "chat", "planning"],
        model: str,
        input_tokens: int,
        output_tokens: int = 0,
        **meta_kwargs
    ) -> None:
        """Helper untuk menambah event dengan parameter sederhana."""
        event = TokenEvent(
            ts=datetime.now(timezone.utc).timestamp(),
            step=step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            meta=meta_kwargs
        )
        self.add(event)
    
    def _update_summary(self) -> None:
        """Update file ringkasan markdown."""
        try:
            if not self.jsonl_path.exists():
                return
            
            # Baca semua events
            events = []
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            
            if not events:
                return
            
            # Hitung statistik
            stats = self._calculate_stats(events)
            
            # Generate ringkasan markdown
            summary_md = self._generate_summary_markdown(stats, events)
            
            # Tulis ke file
            with open(self.summary_path, "w", encoding="utf-8") as f:
                f.write(summary_md)
                
        except Exception as e:
            logger.error(f"Failed to update summary: {e}")
    
    def _calculate_stats(self, events: list) -> Dict[str, Any]:
        """Hitung statistik dari events."""
        total_input = sum(e["input_tokens"] for e in events)
        total_output = sum(e["output_tokens"] for e in events)
        total_tokens = total_input + total_output
        
        # Hitung biaya per model
        cost_by_model = {}
        total_cost = 0.0
        
        for event in events:
            model = event["model"]
            input_tokens = event["input_tokens"]
            output_tokens = event["output_tokens"]
            
            input_price_key = f"{model}_input"
            output_price_key = f"{model}_output"
            
            input_cost = (input_tokens / 1_000_000) * self.prices.get(input_price_key, 0.0)
            output_cost = (output_tokens / 1_000_000) * self.prices.get(output_price_key, 0.0)
            
            event_cost = input_cost + output_cost
            total_cost += event_cost
            
            if model not in cost_by_model:
                cost_by_model[model] = {
                    "input_tokens": 0, "output_tokens": 0, "cost": 0.0
                }
            cost_by_model[model]["input_tokens"] += input_tokens
            cost_by_model[model]["output_tokens"] += output_tokens
            cost_by_model[model]["cost"] += event_cost
        
        # Stats per step
        stats_by_step = {}
        for event in events:
            step = event["step"]
            if step not in stats_by_step:
                stats_by_step[step] = {
                    "count": 0, "input_tokens": 0, "output_tokens": 0
                }
            stats_by_step[step]["count"] += 1
            stats_by_step[step]["input_tokens"] += event["input_tokens"]
            stats_by_step[step]["output_tokens"] += event["output_tokens"]
        
        return {
            "total_events": len(events),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "cost_by_model": cost_by_model,
            "stats_by_step": stats_by_step,
            "latest_event_ts": max(e["ts"] for e in events) if events else 0,
        }
    
    def _generate_summary_markdown(self, stats: Dict[str, Any], events: list) -> str:
        """Generate ringkasan dalam format markdown."""
        now = datetime.now(timezone.utc)
        latest_event = datetime.fromtimestamp(stats["latest_event_ts"], timezone.utc) if stats["latest_event_ts"] else now
        
        md = f"""# Token Usage Summary

**Generated:** {now.strftime("%Y-%m-%d %H:%M:%S UTC")}  
**Latest Event:** {latest_event.strftime("%Y-%m-%d %H:%M:%S UTC")}  
**Total Events:** {stats["total_events"]}

## Overall Statistics

- **Total Tokens:** {stats["total_tokens"]:,}
  - Input: {stats["total_input_tokens"]:,}
  - Output: {stats["total_output_tokens"]:,}
- **Estimated Cost:** ${stats["total_cost_usd"]:.4f} USD

## Usage by Model

| Model | Input Tokens | Output Tokens | Cost (USD) |
|-------|-------------|---------------|------------|
"""
        
        for model, data in stats["cost_by_model"].items():
            md += f"| `{model}` | {data['input_tokens']:,} | {data['output_tokens']:,} | ${data['cost']:.4f} |\n"
        
        md += "\n## Usage by Step\n\n"
        md += "| Step | Count | Input Tokens | Output Tokens |\n"
        md += "|------|-------|-------------|---------------|\n"
        
        for step, data in stats["stats_by_step"].items():
            md += f"| `{step}` | {data['count']} | {data['input_tokens']:,} | {data['output_tokens']:,} |\n"
        
        md += f"\n## Recent Events (Last 10)\n\n"
        md += "| Time | Step | Model | In/Out Tokens |\n"
        md += "|------|------|-------|---------------|\n"
        
        # Tampilkan 10 event terakhir
        recent_events = sorted(events, key=lambda x: x["ts"], reverse=True)[:10]
        for event in recent_events:
            event_time = datetime.fromtimestamp(event["ts"], timezone.utc).strftime("%H:%M:%S")
            md += f"| {event_time} | `{event['step']}` | `{event['model']}` | {event['input_tokens']}/{event['output_tokens']} |\n"
        
        md += f"\n---\n*Generated by RAG v2 Token Ledger*\n"
        
        return md
    
    def get_stats(self) -> Dict[str, Any]:
        """Ambil statistik terkini."""
        try:
            if not self.jsonl_path.exists():
                return {"total_events": 0, "total_tokens": 0, "total_cost_usd": 0.0}
            
            events = []
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            
            return self._calculate_stats(events)
            
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_events": 0, "total_tokens": 0, "total_cost_usd": 0.0, "error": str(e)}


# Global instance
_ledger: Optional[TokenLedger] = None


def get_token_ledger(artefacts_dir: str = "artefacts") -> TokenLedger:
    """Get atau buat global token ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = TokenLedger(artefacts_dir)
    return _ledger


def log_tokens(
    step: Literal["enhancement", "embed", "chat", "planning"],
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    **meta_kwargs
) -> None:
    """Convenience function untuk log token usage."""
    ledger = get_token_ledger()
    ledger.add_simple(step, model, input_tokens, output_tokens, **meta_kwargs)
