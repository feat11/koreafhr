"""
MaxFHR & AMEX 한국 호텔 가격 모니터링 (GitHub Actions용 완전판 - 전체 리포트 전송 수정본)
기능: MaxFHR 수집, AMEX 수집, 매칭, 가격 비교(상승/하락/동일), 역대 최저가 추적, 텔레그램 알림, 자동 저장
"""

import asyncio
import os
import re
import time
import json
import logging
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher

from telegram import Bot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- [설정] ---
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

PRICE_HISTORY_FILE = "price_history.json"
AMEX_LIST_URL = "https://www.americanexpress.com/en-us/travel/discover/property-results/dt/2/d/South%20Korea?ref=search&intlink=US-travel-discover-subnavSearch-location-South%20Korea"

# --- [유틸리티 함수] ---

def load_price_history():
    """저장된 가격 정보 불러오기"""
    if Path(PRICE_HISTORY_FILE).exists():
        try:
            with open(PRICE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_price_history(history):
    """가격 정보 저장하기"""
    try:
        with open(PRICE_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"저장 실패: {e}")

def normalize_hotel_name(name):
    """호텔 이름 통일하기 (매칭 정확도 향상)"""
    if not name: return ""
    name = name.lower()
    name = re.sub(r',\s*an\s*ihg\s*hotel', '', name)
    name = re.sub(r',\s*a\s*luxury\s*collection\s*hotel', '', name)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    return re.sub(r'\s+', ' ', name).strip()

def translate_promo(text):
    """영어 프로모션 한글 번역"""
    if not text: return ""
    if "Complimentary third night" in text: return "3박 시 1박 무료"
    if "Complimentary fourth night" in text: return "4박 시 1박 무료"
    if "25% off" in text: return "25% 할인"
    if "15% off" in text: return "15% 할인"
    return text

def create_driver():
    """서버용 크롬 드라이버 생성"""
    options = Options()
    options.add_argument("--headless=new") # 화면 없이 실행
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return webdriver.Chrome(options=options)

# --- [크롤링 함수] ---

def fetch_maxfhr(driver):
    """MaxFHR 사이트 크롤링"""
    cities = ["Seoul", "Busan", "Jeju"]
    all_hotels = []
    
    try:
        driver.get("https://maxfhr.com")
        time.sleep(3)
        
        for idx, city in enumerate(cities):
            print(f"[{idx+1}/3] MaxFHR: '{city}' 검색 중...")
            if idx > 0: 
                driver.get("https://maxfhr.com")
                time.sleep(2)
            
            # 검색창 찾기
            try:
                inp = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Hotel'], input[placeholder*='Destination'], input.chakra-input"))
                )
                inp.clear()
                inp.send_keys(city)
                time.sleep(1)
                inp.send_keys(Keys.RETURN)
                time.sleep(5)
            except:
                print(f"  ❌ {city} 검색창 찾기 실패")
                continue

            # 스크롤 및 데이터 수집
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            cards = driver.find_elements(By.CSS_SELECTOR, "div.chakra-card")
            if not cards: cards = driver.find_elements(By.TAG_NAME, "article")
            
            count = 0
            for card in cards:
                try:
                    text = card.text
                    html = card.get_attribute('outerHTML').lower()
                    
                    # 호텔명 파싱
                    lines = text.split('\n')
                    if not lines: continue
                    name = lines[0]
                    
                    if "thc" in html or "hotel collection" in html: continue # FHR만 수집
                    
                    # 가격 파싱
                    price_match = re.search(r'\$(\d+)', text)
                    if not price_match: continue
                    price = int(price_match.group(1))
                    
                    # 날짜 파싱
                    date_match = re.search(r'(\d+)/(\d+)/(\d+)', text)
                    earliest = f"{date_match.group(3)}-{date_match.group(1).zfill(2)}-{date_match.group(2).zfill(2)}" if date_match else None
                    
                    # 링크
                    try: link = card.find_element(By.TAG_NAME, "a").get_attribute("href")
                    except: link = "https://maxfhr.com"

                    # 중복 제거 및 추가
                    norm_name = normalize_hotel_name(name)
                    if not any(h['code'] == norm_name for h in all_hotels):
                        all_hotels.append({
                            "code": norm_name,
                            "name": name,
                            "price": price,
                            "earliest": earliest,
                            "url": link,
                            "normalized_name": norm_name
                        })
                        count += 1
                except: continue
            print(f"  -> {count}개 호텔 발견")
            
        return all_hotels
    except Exception as e:
        print(f"❌ MaxFHR 오류: {e}")
        return []

def fetch_amex(driver):
    """AMEX 사이트 크롤링"""
    hotels = []
    try:
        print("AMEX: 데이터 수집 중...")
        driver.get(AMEX_LIST_URL)
        time.sleep(5)
        
        # 팝업 닫기 시도
        try: webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except: pass
        
        # 스크롤
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
        cards = driver.find_elements(By.CSS_SELECTOR, "div.card, div.hotel-card")
        for card in cards:
            try:
                text = card.text
                name = text.split('\n')[0]
                if not name: continue
                
                promo = None
                if "Complimentary" in text or "% off" in text:
                    promo = text.split('\n')[-1] # 대략적인 위치
                    if len(promo) > 50: promo = "프로모션 있음" # 너무 길면 대체

                hotels.append({
                    "name": name,
                    "promo": promo,
                    "normalized_name": normalize_hotel_name(name)
                })
            except: continue
        print(f"  -> {len(hotels)}개 AMEX 정보 발견")
    except Exception as e:
        print(f"⚠️ AMEX 접속 실패 (MaxFHR 데이터만 사용): {e}")
    return hotels

def match_hotels(amex_list, maxfhr_list):
    """두 사이트 호텔 짝지기"""
    matched = []
    # 1. MaxFHR 기준 순회
    for mf in maxfhr_list:
        best_amex = None
        best_score = 0
        
        # AMEX 리스트에서 가장 비슷한 이름 찾기
        for am in amex_list:
            score = SequenceMatcher(None, mf['normalized_name'], am['normalized_name']).ratio()
            if score > best_score:
                best_score = score
                best_amex = am
        
        # 유사도가 높으면 매칭, 아니면 MaxFHR 정보만 사용
        if best_score > 0.6:
            matched.append({"maxfhr": mf, "amex": best_amex})
        else:
            matched.append({"maxfhr": mf, "amex": {"name": mf['name'], "promo": None}})
            
    return matched

# --- [메인 실행 로직 (이 부분이 수정됨!)] ---

async def run():
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHANNEL_CHAT_ID") or os.getenv("PERSONAL_CHAT_ID")
    
    if not token or not chat_id:
        print("❌ 토큰 오류: Secrets 설정을 확인하세요.")
        return

    bot = Bot(token=token)
    driver = create_driver()
    
    try:
        print("🚀 모니터링 시작...")
        
        # 1. 데이터 수집
        maxfhr_data = fetch_maxfhr(driver)
        amex_data = fetch_amex(driver)
        
        if not maxfhr_data:
            print("❌ 호텔 데이터를 하나도 못 가져왔습니다.")
            return

        # 2. 매칭
        final_list = match_hotels(amex_data, maxfhr_data)
        
        # 3. 가격 비교
        prev_history = load_price_history()
        new_history = {}
        
        drop_msgs = []      # 하락
        rise_msgs = []      # 상승
        new_msgs = []       # 신규
        same_msgs = []      # 변동 없음 (★ 추가됨)
        
        print("\n💰 가격 분석 중...")
        for item in final_list:
            mf = item['maxfhr']
            am = item['amex']
            
            code = mf['code']
            price = mf['price']
            name = am['name']
            
            # 기록 확인
            old_price = 999999
            all_time_low = price
            
            is_new = code not in prev_history
            
            if not is_new:
                old_data = prev_history[code]
                old_price = old_data['price']
                all_time_low = min(price, old_data.get('all_time_low', price))
            
            # 히스토리 갱신용 데이터
            new_history[code] = {
                "price": price,
                "name": name,
                "all_time_low": all_time_low,
                "updated": datetime.now().strftime("%Y-%m-%d")
            }
            
            # 메시지 작성
            url_link = f"<a href='{mf['url']}'>{name}</a>"
            promo_txt = f"\n🎁 {translate_promo(am['promo'])}" if am['promo'] else ""
            date_txt = f" ({mf['earliest']})" if mf['earliest'] else ""
            
            # [케이스 1] 가격 하락
            if price < old_price:
                icon = "🔥 역대최저!" if price <= all_time_low else "🔻"
                msg = f"{icon} <b>{name}</b>\n💰 ${old_price} → <b>${price}</b>{date_txt}{promo_txt}"
                drop_msgs.append(msg)
                print(f"  하락: {name} (-${old_price - price})")
                
            # [케이스 2] 가격 상승
            elif price > old_price:
                msg = f"🔺 <b>{name}</b>\n💰 ${old_price} → ${price}{date_txt}"
                rise_msgs.append(msg)
                
            # [케이스 3] 신규 발견
            elif is_new:
                msg = f"🆕 <b>{name}</b>\n💰 <b>${price}</b> 시작{date_txt}{promo_txt}"
                new_msgs.append(msg)

            # [케이스 4] 변동 없음 (★ 추가됨)
            else:
                msg = f"🏨 <b>{name}</b>\n💰 <b>${price}</b>{date_txt}{promo_txt}"
                same_msgs.append(msg)

        # 4. 저장
        save_price_history(new_history)
        
        # 5. 전송 (모든 상태 포함)
        messages = []
        
        # 헤더
        messages.append(f"📅 <b>한국 FHR 호텔 가격 정보</b>\n업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        
        if drop_msgs: 
            messages.append(f"\n<b>📉 가격 하락 ({len(drop_msgs)}개)</b>\n" + "\n\n".join(drop_msgs))
        
        if new_msgs: 
            messages.append(f"\n<b>🆕 신규 발견 ({len(new_msgs)}개)</b>\n" + "\n".join(new_msgs))
            
        if rise_msgs: 
            messages.append(f"\n<b>🔺 가격 상승 ({len(rise_msgs)}개)</b>\n" + "\n".join(rise_msgs))

        # ★ 변동 없음도 무조건 전송
        if same_msgs:
            messages.append(f"\n<b>📌 변동 없음 ({len(same_msgs)}개)</b>\n" + "\n\n".join(same_msgs))
            
        # 메시지 조합 및 전송
        final_msg = "\n" + "="*20 + "\n" + "".join(messages)
        
        if len(final_msg) > 4000:
            for i in range(0, len(final_msg), 4000):
                await bot.send_message(
                    chat_id=chat_id, 
                    text=final_msg[i:i+4000], 
                    parse_mode="HTML", 
                    disable_web_page_preview=True
                )
        else:
            await bot.send_message(
                chat_id=chat_id, 
                text=final_msg, 
                parse_mode="HTML", 
                disable_web_page_preview=True
            )
        print("✅ 전체 리포트 전송 완료")

    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    asyncio.run(run())
