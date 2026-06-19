# ⚽ FIFA World Cup 2026 Score Predictor

A beautiful, lightweight, and mobile-friendly score prediction web application styled in a custom dark emerald green and gold FIFA theme. Built with **Streamlit** (Python) and **SQLite**, designed for quick deployment on the **Streamlit Community Cloud**.

---

## 🏆 App Features

1.  **Time-Aware Prediction Lock:** Predictions must be placed **before 1 hour** of the match kickoff. 
2.  **No Modifications (Locked Scope):** Once a prediction is submitted, the user cannot change it.
3.  **Live Status Countdowns:** Matches are dynamically grouped based on their kickoff times:
    *   **🟢 Open:** Kickoff is > 1 hour away. Users can lock in predictions.
    *   **🔒 Locked / Live:** Kickoff is <= 1 hour away or live. Predictions are locked, and the summary of all users' predictions is visible.
    *   **✅ Finished:** Completed matches display final score, winners, predicted versus actual points awarded, and user prediction summaries.
4.  **Points System:**
    *   **3 Points:** Exact Score predicted (e.g., predicted 2–1, actual 2–1).
    *   **1 Point:** Correct Outcome predicted (e.g., predicted 3–0, actual 1–0).
    *   **0 Points:** Incorrect Outcome.
5.  **Interactive Standings Leaderboard:** Sorts predictors by total points, then by total exact score count.
6.  **Tournament Admin Panel:** Only visible to the `admin` user. Enables inputting actual match scores to finalize matches, update outcomes, and recalculate predictor points instantly.

---

## 🔑 Predefined Accounts

| Username | Password | Role / Predictor Name |
| :--- | :--- | :--- |
| `diego` | `maradona10` | Diego Maradona |
| `leo` | `messi10` | Lionel Messi |
| `kristian` | `ronaldo7` | Cristiano Ronaldo |
| `pele` | `santos10` | Pelé |
| `admin` | `worldcup2026` | **Tournament Admin** (Admin Panel control) |

---

## 🚀 How to Run Locally

If you want to test the app locally, run the following commands in your shell:

```bash
# Navigate to the project folder
cd worldcup_predictor

# Install dependencies
pip install -r requirements.txt

# Start Streamlit application
streamlit run app.py
```

Streamlit will automatically open the dashboard in your default browser at `http://localhost:8501`.

---

## ☁️ How to Host on Streamlit Community Cloud (Free Hosting)

Streamlit Community Cloud allows you to host and deploy the app directly from your GitHub repository for free. Follow these steps:

1.  **Push to GitHub:** Create a new GitHub repository (public or private) and push these project files (`app.py`, `requirements.txt`, `README.md`) into it.
2.  **Sign Up / Log In:** Go to [share.streamlit.io](https://share.streamlit.io/) and connect your GitHub account.
3.  **Deploy App:**
    *   Click the **"New app"** button.
    *   Select your repository, branch (`main`/`master`), and main file path (`app.py`).
    *   Click **"Deploy"**.
4.  **Ready!** Streamlit will automatically install dependencies from `requirements.txt`, initialize the SQLite database, and launch your live World Cup Predictor application.
