"""
한국 FHR 호텔 가격 대시보드
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from storage import HotelStorage
from datetime import datetime

st.set_page_config(page_title="한국 FHR 호텔 가격", layout="wide")

# CSS
st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css");
html, body, [class*="css"] { font-family: 'Pretendard', sans-serif; }

h1 {
    background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
}

.hotel-card {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 10px;
}

.price-down { color: #ff4b4b; }
.price-up { color: #4facfe; }
.price-same { color: #888; }
</style>
""", unsafe_allow_html=True)

# 데이터 로드
@st.cache_data(ttl=3600)
def load_data():
    storage = HotelStorage(base_dir="data")
    history = storage.load_history()
    logs = storage.load_logs()  # 전체 이력!
    return history, logs

# 메인
st.title("📅 한국 FHR 호텔 가격 모니터링")

history, logs = load_data()

if not history:
    st.warning("데이터가 없습니다.")
    st.stop()

# 사이드바 필터
st.sidebar.header("🔍 필터")
cities = ["전체", "서울", "부산", "제주"]
selected_city = st.sidebar.selectbox("도시", cities)

sort_by = st.sidebar.selectbox("정렬", ["이름", "가격 낮은순", "가격 높은순"])

# 탭
tab1, tab2 = st.tabs(["📊 현재 가격", "📈 가격 추이"])

with tab1:
    st.subheader("현재 최저가")
    
    # 호텔 카드
    hotels_df = pd.DataFrame([
        {
            "code": code,
            "name": info["name"],
            "price": info["price"],
            "earliest": info.get("earliest", ""),
            "credit": info.get("credit", 100),
            "all_time_low": info.get("all_time_low", info["price"])
        }
        for code, info in history.items()
    ])
    
    # 필터링
    if selected_city != "전체":
        hotels_df = hotels_df[hotels_df["name"].str.contains(selected_city)]
    
    # 정렬
    if sort_by == "가격 낮은순":
        hotels_df = hotels_df.sort_values("price")
    elif sort_by == "가격 높은순":
        hotels_df = hotels_df.sort_values("price", ascending=False)
    
    # 카드 출력
    for _, hotel in hotels_df.iterrows():
        is_lowest = hotel["price"] == hotel["all_time_low"]
        icon = "🔥" if is_lowest else "🏨"
        
        st.markdown(f"""
        <div class="hotel-card">
            <h3>{icon} {hotel['name']}</h3>
            <div style="font-size: 24px; font-weight: 700; color: {'#ff4b4b' if is_lowest else '#4facfe'}">
                ${hotel['price']}
            </div>
            <div style="color: #888; margin-top: 5px;">
                📅 {hotel['earliest']} | 💳 크레딧: ${hotel['credit']}
            </div>
            {f'<div style="color: #ff4b4b; margin-top: 5px;">✨ 역대 최저가!</div>' if is_lowest else ''}
        </div>
        """, unsafe_allow_html=True)

with tab2:
    st.subheader("가격 추이")
    
    if not logs:
        st.info("아직 이력 데이터가 없습니다. 내일부터 차트가 표시됩니다.")
    else:
        col1, col2 = st.columns([2, 1])
        
        # 호텔 선택
        with col1:
            hotel_names = sorted([info["name"] for info in history.values()])
            selected_hotel = st.selectbox("호텔 선택", hotel_names)
        
        # 기간 선택
        with col2:
            period_days = st.selectbox(
                "기간",
                options=[7, 14, 30, 60, 90, 180, 365, None],
                format_func=lambda x: "전체" if x is None else f"최근 {x}일",
                index=2  # 기본값: 30일
            )
        
        # 해당 호텔의 code 찾기
        hotel_code = None
        for code, info in history.items():
            if info["name"] == selected_hotel:
                hotel_code = code
                break
        
        if hotel_code:
            storage = HotelStorage(base_dir="data")
            # 전체 이력 가져온 후 기간 필터링
            all_history = storage.get_price_history_for_hotel(hotel_code, days=None)
            
            if period_days is not None and len(all_history) > period_days:
                price_history = all_history[-period_days:]
            else:
                price_history = all_history
            
            if price_history:
                # 차트 데이터
                dates = [h["date"] for h in price_history]
                prices = [h["price"] for h in price_history]
                
                # Plotly 차트
                fig = go.Figure()
                
                fig.add_trace(go.Scatter(
                    x=dates,
                    y=prices,
                    mode='lines+markers',
                    name=selected_hotel,
                    line=dict(color='#4facfe', width=3),
                    marker=dict(size=8),
                    hovertemplate="<b>%{x}</b><br>가격: $%{y}<extra></extra>"
                ))
                
                # 역대 최저가 라인
                all_time_low = history[hotel_code]["all_time_low"]
                fig.add_hline(
                    y=all_time_low,
                    line_dash="dash",
                    line_color="red",
                    annotation_text=f"역대 최저 ${all_time_low}",
                    annotation_position="right"
                )
                
                # 평균 라인
                avg_price = sum(prices) / len(prices)
                fig.add_hline(
                    y=avg_price,
                    line_dash="dot",
                    line_color="yellow",
                    annotation_text=f"평균 ${avg_price:.0f}",
                    annotation_position="left"
                )
                
                period_text = f"최근 {period_days}일" if period_days else "전체 기간"
                fig.update_layout(
                    title=f"{selected_hotel} 가격 추이 ({period_text})",
                    xaxis_title="날짜",
                    yaxis_title="가격 ($)",
                    height=500,
                    template="plotly_dark",
                    hovermode="x unified"
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 통계
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("현재 가격", f"${prices[-1]}")
                col2.metric("평균 가격", f"${avg_price:.0f}")
                col3.metric("최저 가격", f"${min(prices)}")
                col4.metric("최고 가격", f"${max(prices)}")
                
                # 가격 변동 폭
                price_change = prices[-1] - prices[0] if len(prices) > 1 else 0
                change_pct = (price_change / prices[0] * 100) if prices[0] != 0 else 0
                
                st.info(f"📊 **기간 내 변동:** ${price_change:+.0f} ({change_pct:+.1f}%)")
            else:
                st.info("이력 데이터가 아직 충분하지 않습니다.")

# 푸터
st.markdown("---")
st.caption(f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
st.caption("데이터 출처: MaxFHR, AMEX Fine Hotels & Resorts")
