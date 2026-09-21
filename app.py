cat << 'EOF' > app.py
import streamlit as st
import requests
import json
import base64

st.set_page_config(page_title="US Earnings Watchlist", page_icon="📈", layout="centered")
st.title("📈 US Earnings Watchlist Manager")

GH_PAT = st.secrets.get("GH_PAT")
GITHUB_REPO = st.secrets.get("GITHUB_REPOSITORY")

if not GH_PAT or not GITHUB_REPO:
    st.error("Missing GitHub credentials in Streamlit Secrets.")
    st.stop()

HEADERS = {
    "Authorization": f"token {GH_PAT}",
    "Accept": "application/vnd.github.v3+json"
}
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/watchlist.json"

def fetch_watchlist():
    res = requests.get(API_URL, headers=HEADERS)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    else:
        st.error(f"Failed to fetch data: {res.text}")
        return None, None

def commit_watchlist(new_data, sha):
    encoded_content = base64.b64encode(json.dumps(new_data, indent=2).encode("utf-8")).decode("utf-8")
    payload = {
        "message": "Update watchlist via Streamlit Dashboard",
        "content": encoded_content,
        "sha": sha
    }
    res = requests.put(API_URL, headers=HEADERS, json=payload)
    return res.status_code in [200, 201]

config_data, file_sha = fetch_watchlist()

if config_data:
    watchlist = config_data.get("watchlist", [])
    
    st.subheader(f"Current Watchlist ({len(watchlist)} Tickers)")
    st.write(", ".join(watchlist))
    st.divider()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("➕ Add Ticker")
        new_ticker = st.text_input("Enter Symbol (e.g., AAPL):").strip().upper()
        if st.button("Add to Watchlist", use_container_width=True):
            if new_ticker and new_ticker not in watchlist:
                config_data["watchlist"].append(new_ticker)
                if commit_watchlist(config_data, file_sha):
                    st.success(f"Added {new_ticker}!")
                    st.rerun()
            elif new_ticker in watchlist:
                st.warning(f"{new_ticker} is already tracked.")

    with col2:
        st.subheader("🗑️ Remove Ticker")
        remove_ticker = st.selectbox("Select ticker:", [""] + watchlist)
        if st.button("Remove from Watchlist", use_container_width=True):
            if remove_ticker:
                config_data["watchlist"].remove(remove_ticker)
                if commit_watchlist(config_data, file_sha):
                    st.success(f"Removed {remove_ticker}!")
                    st.rerun()

    st.divider()
    st.subheader("⚙️ Global Strategy Settings")
    min_days = st.number_input("Minimum Days to Earnings", value=config_data.get("window_min_days", 7))
    max_days = st.number_input("Maximum Days to Earnings", value=config_data.get("window_max_days", 15))
    
    if st.button("Save Settings", type="primary"):
        config_data["window_min_days"] = min_days
        config_data["window_max_days"] = max_days
        if commit_watchlist(config_data, file_sha):
            st.success("Settings saved successfully!")
            st.rerun()
EOF
