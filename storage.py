"""
데이터 저장/로드 모듈
- price_history.json: 현재 가격
- price_log.jsonl: 일별 이력 (JSONL 형식)
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 업데이트 없이 history에 유지할 최대 일수
STALE_DAYS = 7


class HotelStorage:
    """호텔 가격 데이터 저장소"""
    
    def __init__(self, base_dir: str = "data"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        
        self.history_file = self.base_dir / "price_history.json"
        self.log_file = self.base_dir / "price_log.jsonl"
        self._logs_cache = None  # load_logs 반복 호출 방지
    
    def load_history(self) -> Dict:
        """현재 가격 정보 로드"""
        if not self.history_file.exists():
            return {}
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ history 로드 실패: {e}")
            return {}
    
    def save_history(self, new_data: Dict, prev_data: Dict = None) -> None:
        """
        가격 정보 저장 (merge 방식)
        
        - 기존 history를 base로, 오늘 수집된 호텔만 업데이트
        - STALE_DAYS 이상 업데이트 없는 호텔은 자동 제거
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        if prev_data is None:
            prev_data = {}
        
        # merge: 기존 데이터 + 새 데이터 덮어쓰기
        merged = dict(prev_data)
        merged.update(new_data)
        
        # stale 호텔 제거 (STALE_DAYS 이상 업데이트 없는 항목)
        cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
        stale_keys = [
            k for k, v in merged.items()
            if v.get("updated", today_str) < cutoff
        ]
        for k in stale_keys:
            print(f"  🗑️ stale 호텔 제거: {merged[k].get('name', k)} (마지막 업데이트: {merged[k].get('updated')})")
            del merged[k]
        
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ 저장 실패: {e}")
    
    def append_log(self, hotels: List[Dict], prev_count: int = 0) -> None:
        """
        일별 가격 이력 추가 (JSONL 형식)
        
        각 줄: {"date": "2026-01-01", "hotels": [...], "partial": false}
        
        수집 호텔 수가 이전 대비 절반 이하이면 partial=true로 마킹
        """
        is_partial = prev_count > 0 and len(hotels) < prev_count * 0.5
        
        log_entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now().isoformat(),
            "hotels": hotels,
        }
        if is_partial:
            log_entry["partial"] = True
            print(f"⚠️ 부분 수집 감지: {len(hotels)}개 (이전: {prev_count}개) → 로그에 partial 마킹")
        
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            self._logs_cache = None  # 캐시 무효화
        except Exception as e:
            print(f"⚠️ 로그 저장 실패: {e}")
    
    def load_logs(self, days: Optional[int] = None) -> List[Dict]:
        """
        이력 로그 읽기 (캐싱 지원)
        
        Args:
            days: 최근 N일 (None이면 전체 이력)
        
        Returns:
            [{"date": "2026-01-01", "hotels": [...]}, ...]
        """
        if self._logs_cache is None:
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
            
            self._logs_cache = logs
        
        logs = self._logs_cache
        if days is not None:
            logs = logs[-days:]
        
        return logs
    
    def get_all_time_low(self, hotel_code: str, exclude_date: str = None) -> Optional[Dict]:
        """
        특정 호텔의 역대 최저가 조회 (특정 날짜 제외)
        
        price_log.jsonl의 전체 이력에서 가장 낮은 가격과 해당 날짜를 반환.
        partial=true인 로그 엔트리는 건너뜀 (부분 수집 데이터 오염 방지).
        
        Args:
            hotel_code: 호텔 코드 (normalized name)
            exclude_date: 제외할 날짜 (예: 오늘 "2026-02-16")
        
        Returns:
            {"price": 280, "date": "2026-01-15", "earliest": "2026-03-01"} or None
        """
        logs = self.load_logs()
        best = None
        
        for log in logs:
            log_date = log.get("date")
            if exclude_date and log_date == exclude_date:
                continue
            # partial 로그는 신뢰할 수 없으므로 건너뜀
            if log.get("partial"):
                continue
            
            for hotel in log.get("hotels", []):
                if hotel.get("code") == hotel_code:
                    p = hotel.get("price")
                    if p is not None:
                        if best is None or p < best["price"]:
                            best = {
                                "price": p,
                                "date": log_date,
                                "earliest": hotel.get("earliest"),
                            }
                    break  # 같은 날짜에 같은 호텔은 1개
        
        return best
    
    def get_price_history_for_hotel(self, hotel_code: str, days: Optional[int] = None) -> List[Dict]:
        """
        특정 호텔의 가격 이력 추출
        
        Args:
            days: 최근 N일 (None이면 전체 이력)
        
        Returns:
            [{"date": "2026-01-01", "price": 311, "earliest": "..."}, ...]
        """
        logs = self.load_logs(days=None)
        history = []
        
        for log in logs:
            # partial 로그 건너뜀
            if log.get("partial"):
                continue
            for hotel in log.get("hotels", []):
                if hotel.get("code") == hotel_code:
                    history.append({
                        "date": log["date"],
                        "price": hotel.get("price"),
                        "earliest": hotel.get("earliest"),
                        "credit": hotel.get("credit")
                    })
                    break
        
        if days is not None and len(history) > days:
            history = history[-days:]
        
        return history
