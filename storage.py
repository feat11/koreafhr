"""
데이터 저장/로드 모듈
- price_history.json: 현재 가격
- price_log.jsonl: 일별 이력 (JSONL 형식)
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


class HotelStorage:
    """호텔 가격 데이터 저장소"""
    
    def __init__(self, base_dir: str = "data"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        
        self.history_file = self.base_dir / "price_history.json"
        self.log_file = self.base_dir / "price_log.jsonl"
    
    def load_history(self) -> Dict:
        """현재 가격 정보 로드"""
        if not self.history_file.exists():
            return {}
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def save_history(self, data: Dict) -> None:
        """현재 가격 정보 저장"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ 저장 실패: {e}")
    
    def append_log(self, hotels: List[Dict]) -> None:
        """
        일별 가격 이력 추가 (JSONL 형식)
        
        각 줄: {"date": "2026-01-01", "hotels": [...]}
        """
        log_entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now().isoformat(),
            "hotels": hotels
        }
        
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"⚠️ 로그 저장 실패: {e}")
    
    def load_logs(self, days: Optional[int] = None) -> List[Dict]:
        """
        이력 로그 읽기
        
        Args:
            days: 최근 N일 (None이면 전체 이력)
        
        Returns:
            [{"date": "2026-01-01", "hotels": [...]}, ...]
        """
        if not self.log_file.exists():
            return []
        
        logs = []
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
        except Exception as e:
            print(f"⚠️ 로그 로드 실패: {e}")
            return []
        
        # days 지정 시에만 필터링
        if days is not None:
            logs = logs[-days:]
        
        return logs
    
    def get_price_history_for_hotel(self, hotel_code: str, days: Optional[int] = None) -> List[Dict]:
        """
        특정 호텔의 가격 이력 추출
        
        Args:
            days: 최근 N일 (None이면 전체 이력)
        
        Returns:
            [{"date": "2026-01-01", "price": 311, "earliest": "..."}, ...]
        """
        logs = self.load_logs(days=None)  # 일단 전체 로드
        history = []
        
        for log in logs:
            for hotel in log.get("hotels", []):
                if hotel.get("code") == hotel_code:
                    history.append({
                        "date": log["date"],
                        "price": hotel.get("price"),
                        "earliest": hotel.get("earliest"),
                        "credit": hotel.get("credit")
                    })
                    break
        
        # days 지정 시에만 필터링
        if days is not None and len(history) > days:
            history = history[-days:]
        
        return history
