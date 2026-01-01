"""
MaxFHR & AMEX 한국 호텔 가격 모니터링 (GitHub Actions용 - 타임아웃 개선 버전)
기능: MaxFHR 수집, AMEX 수집, 매칭, 가격 비교(상승/하락/동일), 역대 최저가 추적, 텔레그램 알림, 자동 저장
수정: WebDriverWait 15초, 재시도 3회, 페이지 로딩 시간 증가
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

# dotenv 로드 추가 (로컬용)
try:
    from dotenv import load_dotenv
    load_dotenv("key.env")
except:
    pass  # GitHub Actions에서는 환경변수 직접 설정됨

from telegram import Bot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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
    """영어 프로모션 한글 번역 (날짜 정보 포함)"""
    if not text: return ""
    
    # .1, .2 같은 숫자 먼저 제거
    text = re.sub(r'\.\d+', '', text)
    
    # 번역
    translated = text
    if "Complimentary third night" in text:
        translated = text.replace("Complimentary third night", "3박 시 1박 무료")
    elif "Complimentary fourth night" in text:
        translated = text.replace("Complimentary fourth night", "4박 시 1박 무료")
    elif "25% off" in text:
        translated = "25% 할인"
    elif "15% off" in text:
        translated = "15% 할인"
    
    # 날짜 정보 한글화 (Book by ... for travel by ...)
    match = re.search(r'Book by (\d{2}/\d{2}/\d{4}) for travel by (\d{2}/\d{2}/\d{4})', translated)
    if match:
        book_by = match.group(1)
        travel_by = match.group(2)
        
        # MM/DD/YYYY → YYYY-MM-DD 변환
        book_date = datetime.strptime(book_by, "%m/%d/%Y").strftime("%Y-%m-%d")
        travel_date = datetime.strptime(travel_by, "%m/%d/%Y").strftime("%Y-%m-%d")
        
        # 날짜 정보 추가
        date_info = f" (예약마감: {book_date}, 여행기간: ~{travel_date})"
        
        # "Book by..." 부분 제거하고 날짜 정보 추가
        translated = re.sub(r'\s*Book by.*', date_info, translated)
    
    # 줄 바꿈 제거
    translated = translated.replace('\n', ' ').strip()
    
    return translated

def create_driver():
    """서버용 크롬 드라이버 생성 (GitHub Actions 최적화)"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Chrome 바이너리 경로 찾기 (GitHub Actions 대응)
    import shutil
    chrome_paths = [
        "/usr/bin/chromium-browser",  # Ubuntu
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    
    chrome_binary = None
    for path in chrome_paths:
        if os.path.exists(path):
            chrome_binary = path
            break
    
    if not chrome_binary:
        chrome_binary = shutil.which("chromium-browser") or shutil.which("google-chrome")
    
    if chrome_binary:
        options.binary_location = chrome_binary
        print(f"Chrome 바이너리: {chrome_binary}")
    
    return webdriver.Chrome(options=options)

# --- [크롤링 함수] ---

def fetch_maxfhr(driver, retry=3):
    """MaxFHR 사이트 크롤링 (재시도 로직 추가)"""
    
    for attempt in range(retry):
        try:
            cities = ["Seoul", "Busan", "Jeju"]
            all_hotels = []
            
            print(f"MaxFHR 접속 시도 ({attempt+1}/{retry})...")
            driver.get("https://maxfhr.com")
            time.sleep(5)  # 3초 → 5초 증가
            
            for idx, city in enumerate(cities):
                print(f"  [{idx+1}/3] '{city}' 검색 중...")
                if idx > 0: 
                    driver.get("https://maxfhr.com")
                    time.sleep(3)  # 2초 → 3초 증가
                
                # 검색창 찾기 (타임아웃 15초)
                try:
                    inp = WebDriverWait(driver, 15).until(  # 5초 → 15초 증가
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Hotel'], input[placeholder*='Destination'], input.chakra-input"))
                    )
                    inp.clear()
                    inp.send_keys(city)
                    time.sleep(2)  # 1초 → 2초 증가
                    inp.send_keys(Keys.RETURN)
                    time.sleep(10)  # 5초 → 10초 증가 (가장 중요!)
                except TimeoutException:
                    print(f"    ⚠️ {city} 검색창 찾기 실패 (타임아웃)")
                    continue

                # 스크롤 및 데이터 수집
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(4)  # 2초 → 4초 증가
                
                cards = driver.find_elements(By.CSS_SELECTOR, "div.chakra-card")
                if not cards: 
                    cards = driver.find_elements(By.TAG_NAME, "article")
                
                count = 0
                for card in cards:
                    try:
                        text = card.text
                        html = card.get_attribute('outerHTML').lower()
                        
                        # 호텔명 파싱
                        lines = text.split('\n')
                        if not lines: continue
                        name = lines[0]
                        
                        if "thc" in html or "hotel collection" in html: 
                            continue  # FHR만 수집
                        
                        # 가격 파싱
                        price_match = re.search(r'\$(\d+)', text)
                        if not price_match: continue
                        price = int(price_match.group(1))
                        
                        # 날짜 파싱
                        date_match = re.search(r'(\d+)/(\d+)/(\d+)', text)
                        earliest = f"{date_match.group(3)}-{date_match.group(1).zfill(2)}-{date_match.group(2).zfill(2)}" if date_match else None
                        
                        # 크레딧 파싱
                        credit = None
                        credit_match = re.search(r'USD\$(\d+)', text)
                        if credit_match:
                            credit = int(credit_match.group(1))
                        
                        # 링크
                        try: 
                            link = card.find_element(By.TAG_NAME, "a").get_attribute("href")
                        except: 
                            link = "https://maxfhr.com"

                        # 중복 제거 및 추가
                        norm_name = normalize_hotel_name(name)
                        if not any(h['code'] == norm_name for h in all_hotels):
                            all_hotels.append({
                                "code": norm_name,
                                "name": name,
                                "price": price,
                                "earliest": earliest,
                                "credit": credit,
                                "url": link,
                                "normalized_name": norm_name
                            })
                            count += 1
                    except: 
                        continue
                        
                print(f"    ✓ {count}개 호텔 발견")
            
            # 성공 시 반환
            if all_hotels:
                print(f"✅ MaxFHR 수집 성공: {len(all_hotels)}개 호텔")
                return all_hotels
            else:
                raise Exception("호텔 데이터 0개")
                
        except Exception as e:
            if attempt < retry - 1:
                print(f"⚠️ MaxFHR 재시도 중... ({attempt+1}/{retry}) - {e}")
                time.sleep(10)  # 10초 대기 후 재시도
                continue
            else:
                print(f"❌ MaxFHR 최종 실패: {e}")
                return []
    
    return []

def fetch_amex(driver, retry=3):
    """AMEX 사이트 크롤링 (재시도 로직 추가)"""
    
    for attempt in range(retry):
        try:
            print(f"AMEX 접속 시도 ({attempt+1}/{retry})...")
            driver.get(AMEX_LIST_URL)
            time.sleep(8)  # 5초 → 8초 증가
            
            # 팝업 닫기 시도
            try: 
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except: 
                pass
            
            # 스크롤
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)  # 2초 → 3초 증가
                
            cards = driver.find_elements(By.CSS_SELECTOR, "div.card, div.hotel-card")
            print(f"  → {len(cards)}개 카드 발견")
            hotels = []
            
            for idx, card in enumerate(cards):
                try:
                    text = card.text
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    if not lines: continue
                    
                    # 호텔명 찾기 (개선)
                    name = None
                    skip_keywords = [
                        "FINE HOTELS",
                        "THE HOTEL COLLECTION",
                        "ANDAZ",
                        "CONRAD HOTELS & RESORTS",
                        "FAIRMONT",
                        "FOUR SEASONS HOTELS AND RESORTS",
                        "GRAND HYATT",
                        "PARK HYATT",
                        "LOTTE HOTELS & RESORTS",
                        "LUXURY COLLECTION",
                        "IHG",
                        "MARRIOTT"
                    ]
                    
                    for line in lines:
                        # 대문자 카테고리명 스킵
                        if line.isupper() and any(skip in line for skip in skip_keywords):
                            continue
                        # 위치 정보 스킵
                        if "South Korea" in line or line == "Korea":
                            continue
                        # 설명문 스킵
                        if len(line) > 50:
                            continue
                        # 호텔명 찾음!
                        if line and not line.startswith("Book") and not line.startswith("Complimentary"):
                            name = line
                            break
                    
                    if not name: continue
                    
                    # 프로모션 찾기 (날짜 정보 포함)
                    promo_parts = []
                    i = 0
                    while i < len(lines):
                        line = lines[i]
                        # 프로모션 시작
                        if any(keyword in line for keyword in [
                            "Complimentary third night",
                            "Complimentary fourth night",
                            "% off",
                            "Special Offer"
                        ]):
                            promo_parts.append(line)
                            # 다음 줄도 프로모션 관련이면 추가
                            if i + 1 < len(lines):
                                next_line = lines[i + 1]
                                if "Book by" in next_line or "for travel" in next_line:
                                    promo_parts.append(next_line)
                            break
                        i += 1
                    
                    promo = " ".join(promo_parts) if promo_parts else None

                    hotels.append({
                        "name": name,
                        "promo": promo,
                        "normalized_name": normalize_hotel_name(name)
                    })
                except Exception as e:
                    continue
                    
            if hotels:
                print(f"✅ AMEX 수집 성공: {len(hotels)}개 호텔")
                return hotels
            else:
                raise Exception("호텔 데이터 0개")
                
        except Exception as e:
            if attempt < retry - 1:
                print(f"⚠️ AMEX 재시도 중... ({attempt+1}/{retry}) - {e}")
                time.sleep(10)
                continue
            else:
                print(f"⚠️ AMEX 최종 실패 (MaxFHR만 사용): {e}")
                return []
    
    return []

def match_hotels(amex_list, maxfhr_list):
    """두 사이트 호텔 짝지기"""
    matched = []
    
    for mf in maxfhr_list:
        best_amex = None
        best_score = 0
        
        for am in amex_list:
            score = SequenceMatcher(None, mf['normalized_name'], am['normalized_name']).ratio()
            if score > best_score:
                best_score = score
                best_amex = am
        
        if best_score > 0.6:
            matched.append({"maxfhr": mf, "amex": best_amex})
        else:
            matched.append({"maxfhr": mf, "amex": {"name": mf['name'], "promo": None}})
            
    return matched

# --- [메인 실행 로직] ---

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
        
        # 1. 데이터 수집 (재시도 3회)
        maxfhr_data = fetch_maxfhr(driver, retry=3)
        amex_data = fetch_amex(driver, retry=3)
        
        if not maxfhr_data:
            print("❌ 호텔 데이터를 하나도 못 가져왔습니다.")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ MaxFHR 접속 실패 (타임아웃)\n다음 실행 시 재시도됩니다.",
                parse_mode="HTML"
            )
            return

        # 2. 매칭
        final_list = match_hotels(amex_data, maxfhr_data)
        
        # 3. 가격 비교
        prev_history = load_price_history()
        new_history = {}
        
        drop_msgs = []
        rise_msgs = []
        new_msgs = []
        same_msgs = []
        
        print("\n💰 가격 분석 중...")
        for item in final_list:
            mf = item['maxfhr']
            am = item['amex']
            
            code = mf['code']
            price = mf['price']
            name = mf['name']  # MaxFHR 이름 사용 (더 정확함)
            
            old_price = 999999
            all_time_low = price
            
            is_new = code not in prev_history
            
            if not is_new:
                old_data = prev_history[code]
                old_price = old_data['price']
                all_time_low = min(price, old_data.get('all_time_low', price))
            
            new_history[code] = {
                "price": price,
                "name": name,
                "earliest": mf.get('earliest'),  # 날짜 저장 추가
                "all_time_low": all_time_low,
                "updated": datetime.now().strftime("%Y-%m-%d")
            }
            
            # 메시지 작성
            promo_txt = f"\n🎁 {translate_promo(am['promo'])}" if am['promo'] else ""
            date_txt = f" ({mf['earliest']})" if mf['earliest'] else ""
            credit_txt = f"\n💳 크레딧: ${mf.get('credit', 100)}"
            
            # 이전 날짜 가져오기
            old_date_txt = ""
            if not is_new and 'earliest' in prev_history[code]:
                old_date = prev_history[code]['earliest']
                if old_date:
                    old_date_txt = f" ({old_date})"
            
            # 가격 하락
            if price < old_price:
                # 역대 최저가인 경우
                if price <= all_time_low:
                    msg = f"🔥 역대최저! <a href='{mf['url']}'>{name}</a>\n💰 최저가: <b>${price}</b>{date_txt}\n🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}\n✨ <b>역대 최저가</b>{promo_txt}"
                else:
                    msg = f"🔻 <a href='{mf['url']}'>{name}</a>\n💰 최저가: <b>${price}</b>{date_txt}\n🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}{promo_txt}"
                drop_msgs.append(msg)
                print(f"  하락: {name} (-${old_price - price})")
                
            # 가격 상승
            elif price > old_price:
                msg = f"🔺 <a href='{mf['url']}'>{name}</a>\n💰 최저가: <b>${price}</b>{date_txt}\n🔺 직전 최저가: ${old_price}{old_date_txt}{credit_txt}"
                rise_msgs.append(msg)
                
            # 신규 발견
            elif is_new:
                msg = f"🆕 <a href='{mf['url']}'>{name}</a>\n💰 최저가: <b>${price}</b>{date_txt}{credit_txt}{promo_txt}"
                new_msgs.append(msg)

            # 변동 없음
            else:
                msg = f"🏨 <a href='{mf['url']}'>{name}</a>\n💰 최저가: <b>${price}</b>{date_txt}\n🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}{promo_txt}"
                same_msgs.append(msg)

        # 4. 저장
        save_price_history(new_history)
        
        # 5. 전송
        messages = []
        messages.append(f"📅 <b>한국 FHR 호텔 가격 정보</b>\n업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        
        if drop_msgs: 
            messages.append(f"\n<b>📉 가격 하락 ({len(drop_msgs)}개)</b>\n\n" + "\n\n".join(drop_msgs))
        
        if new_msgs: 
            messages.append(f"\n<b>🆕 신규 발견 ({len(new_msgs)}개)</b>\n\n" + "\n\n".join(new_msgs))
            
        if rise_msgs: 
            messages.append(f"\n<b>🔺 가격 상승 ({len(rise_msgs)}개)</b>\n\n" + "\n\n".join(rise_msgs))

        if same_msgs:
            messages.append(f"\n<b>📌 변동 없음 ({len(same_msgs)}개)</b>\n\n" + "\n\n".join(same_msgs))
            
        final_msg = "".join(messages)
        
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
        if driver: 
            driver.quit()

if __name__ == "__main__":
    asyncio.run(run())
