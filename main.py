"""
MaxFHR & AMEX 한국 호텔 가격 모니터링 (GitHub Actions용)
기능: MaxFHR 수집, AMEX 수집, 매칭, 가격 비교(상승/하락/동일), 역대 최저가 추적, 텔레그램 알림, 가격 이력 누적
수정: 전체 가격 이력 누적, Streamlit 대시보드 지원, 타임아웃 증가
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
from storage import HotelStorage

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
from selenium.webdriver.common.page_load_strategy import PageLoadStrategy

# --- [설정] ---
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

PRICE_HISTORY_FILE = "data/price_history.json"
AMEX_LIST_URL = "https://www.americanexpress.com/en-us/travel/discover/property-results/dt/2/d/South%20Korea?ref=search&intlink=US-travel-discover-subnavSearch-location-South%20Korea"

# --- [유틸리티 함수] ---

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

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def clean_promo(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"#+", "", s)
    s = clean_text(s)
    return s

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
    
    # [핵심 1] 페이지 로딩 전략 'eager' - DOM만 로드되면 진행
    options.page_load_strategy = 'eager'
    
    # [핵심 2] 이미지 로딩 차단 - 메모리 및 네트워크 절약
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    })
    
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
    
    driver = webdriver.Chrome(options=options)
    
    # [핵심 3] 페이지 로드 타임아웃 설정
    driver.set_page_load_timeout(60)
    
    return driver

# --- [크롤링 함수] ---

def fetch_maxfhr(driver, retry=3):
    """MaxFHR 사이트 크롤링 (타임아웃 방어 로직)"""
    
    for attempt in range(retry):
        try:
            cities = ["Seoul", "Busan", "Jeju"]
            all_hotels = []
            
            print(f"MaxFHR 접속 시도 ({attempt+1}/{retry})...")
            
            # 메인 페이지 접속 (타임아웃 방어)
            try:
                driver.get("https://maxfhr.com")
            except TimeoutException:
                print("  ⚠️ 메인 페이지 로딩 지연 (진행 계속)")
                driver.execute_script("window.stop();")
            
            time.sleep(5)
            
            for idx, city in enumerate(cities):
                print(f"  [{idx+1}/3] '{city}' 검색 중...")
                if idx > 0: 
                    try:
                        driver.get("https://maxfhr.com")
                        time.sleep(3)
                    except:
                        pass
                
                # 검색창 찾기
                try:
                    inp = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Hotel'], input[placeholder*='Destination'], input.chakra-input"))
                    )
                    inp.clear()
                    inp.send_keys(city)
                    time.sleep(1)
                    
                    # [핵심] 엔터 키 예외 처리
                    try:
                        inp.send_keys(Keys.RETURN)
                    except Exception:
                        print("  ⚠️ 엔터 키 입력 중 지연 (무시하고 진행)")
                        pass
                        
                    time.sleep(8)

                except TimeoutException:
                    print(f"    ⚠️ {city} 검색창 찾기 실패 (타임아웃)")
                    continue
                except Exception as e:
                    print(f"    ⚠️ {city} 검색 중 오류: {e}")
                    continue

                # 스크롤
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)
                except:
                    pass
                
                # 데이터 수집
                cards = driver.find_elements(By.CSS_SELECTOR, "div.chakra-card")
                if not cards: 
                    cards = driver.find_elements(By.TAG_NAME, "article")
                
                count = 0
                for card in cards:
                    try:
                        text = card.text
                        html = card.get_attribute('outerHTML').lower()
                        
                        lines = text.split('\n')
                        if not lines: continue
                        name = lines[0]
                        
                        if "thc" in html or "hotel collection" in html: 
                            continue
                        
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
                time.sleep(10)
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

def build_section(title: str, items: list) -> str:
    """
    섹션 출력 규칙:
    - 섹션 자체가 없으면 아예 출력 안 함
    - 섹션 있으면 "위 1줄(빈줄) + 제목 + 아래 1줄(빈줄) + 내용" 형태로 고정
    - 각 아이템은 빈줄 1개로 구분
    """
    if not items:
        return ""

    body = "\n\n".join(items).strip()
    return f"\n\n<b>{title} ({len(items)}개)</b>\n\n{body}"


async def run():
    # Storage 초기화
    storage = HotelStorage(base_dir="data")
    
    token = os.getenv("TELEGRAM_TOKEN")

    channel_id = os.getenv("CHANNEL_CHAT_ID")
    personal_id = os.getenv("PERSONAL_CHAT_ID")

    # yml에서 넘겨주는 값: schedule이면 channel, 수동이면 personal(기본)
    target = (os.getenv("TARGET") or "personal").strip().lower()

    if not token:
        print("❌ TELEGRAM_TOKEN 없음: Secrets 설정을 확인하세요.")
        return

    chat_id = channel_id if target == "channel" else personal_id

    if not chat_id:
        print(f"❌ chat_id 없음 (TARGET={target}). Secrets 설정을 확인하세요.")
        return

    bot = Bot(token=token)
    driver = create_driver()

    try:
        print("🚀 모니터링 시작...")

        # 1) 데이터 수집
        maxfhr_data = fetch_maxfhr(driver, retry=3)
        amex_data = fetch_amex(driver, retry=3)

        if not maxfhr_data:
            print("❌ 호텔 데이터를 하나도 못 가져왔습니다.")
            if target != "channel":
                await bot.send_message(
                    chat_id=chat_id,
                    text="❌ MaxFHR 접속 실패 (타임아웃)\n다음 실행 시 재시도됩니다.",
                    parse_mode="HTML",
                )
            return

        # 2) 매칭
        final_list = match_hotels(amex_data, maxfhr_data)

        # 3) 가격 비교
        prev_history = storage.load_history()
        new_history = {}

        drop_msgs, rise_msgs, new_msgs, same_msgs = [], [], [], []
        hotels_snapshot = []  # 이력용 스냅샷

        print("\n💰 가격 분석 중...")
        for item in final_list:
            mf = item["maxfhr"]
            am = item["amex"]

            code = mf["code"]
            price = mf["price"]
            name = mf["name"]

            is_new = code not in prev_history

            old_price = prev_history.get(code, {}).get("price", 999999)
            prev_low = prev_history.get(code, {}).get("all_time_low", price)
            all_time_low = min(price, prev_low)

            credit_val = mf.get("credit")
            credit_display = credit_val if credit_val is not None else 100

            new_history[code] = {
                "price": price,
                "name": name,
                "earliest": mf.get("earliest"),
                "all_time_low": all_time_low,
                "updated": datetime.now().strftime("%Y-%m-%d"),
                "credit": credit_display,
                "credit_inferred": credit_val is None,
            }
            
            # 이력 스냅샷 추가
            hotels_snapshot.append({
                "code": code,
                "name": name,
                "price": price,
                "earliest": mf.get("earliest"),
                "credit": credit_display,
            })

            # 텍스트 조립
            promo = am.get("promo")
            promo_kr = translate_promo(promo) if promo else ""
            promo_kr = clean_promo(promo_kr) if promo_kr else ""
            promo_txt = f"\n🎁 {promo_kr}" if promo_kr else ""

            date_txt = f" ({mf['earliest']})" if mf.get("earliest") else ""
            credit_txt = f"\n💳 크레딧: ${credit_display}"

            old_date = prev_history.get(code, {}).get("earliest")
            old_date_txt = f" ({old_date})" if old_date else ""

            # 하락/상승/신규/동일 분류
            if price < old_price:
                if price <= all_time_low:
                    msg = (
                        f"🔥 역대최저! <a href='{mf['url']}'>{name}</a>\n"
                        f"💰 최저가: <b>${price}</b>{date_txt}\n"
                        f"🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}\n"
                        f"✨ <b>역대 최저가</b>{promo_txt}"
                    )
                else:
                    msg = (
                        f"🔻 <a href='{mf['url']}'>{name}</a>\n"
                        f"💰 최저가: <b>${price}</b>{date_txt}\n"
                        f"🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}{promo_txt}"
                    )
                drop_msgs.append(msg)
                print(f"  하락: {name} (-${old_price - price})")

            elif price > old_price:
                msg = (
                    f"🔺 <a href='{mf['url']}'>{name}</a>\n"
                    f"💰 최저가: <b>${price}</b>{date_txt}\n"
                    f"🔺 직전 최저가: ${old_price}{old_date_txt}{credit_txt}{promo_txt}"
                )
                rise_msgs.append(msg)

            elif is_new:
                msg = (
                    f"🆕 <a href='{mf['url']}'>{name}</a>\n"
                    f"💰 최저가: <b>${price}</b>{date_txt}{credit_txt}{promo_txt}"
                )
                new_msgs.append(msg)

            else:
                msg = (
                    f"🏨 <a href='{mf['url']}'>{name}</a>\n"
                    f"💰 최저가: <b>${price}</b>{date_txt}\n"
                    f"🔻 직전 최저가: ${old_price}{old_date_txt}{credit_txt}{promo_txt}"
                )
                same_msgs.append(msg)

        # 4) 저장
        storage.save_history(new_history)
        storage.append_log(hotels_snapshot)

        # 5) 전송 (섹션 위/아래 1칸씩 고정)
        header = (
            f"📅 <b>한국 FHR 호텔 가격 정보</b>\n"
            f"업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

        final_msg = (
            header
            + build_section("📉 가격 하락", drop_msgs)
            + build_section("🆕 신규 발견", new_msgs)
            + build_section("🔺 가격 상승", rise_msgs)
            + build_section("📌 변동 없음", same_msgs)
        ).rstrip()

        # 텔레그램 4096 제한 대비
        if len(final_msg) > 4000:
            for i in range(0, len(final_msg), 4000):
                await bot.send_message(
                    chat_id=chat_id,
                    text=final_msg[i:i + 4000],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=final_msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
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
