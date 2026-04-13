from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import os
import json
import asyncio

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(BASE_DIR, "data", "questions.json"), "r", encoding="utf-8") as f:
    questions = json.load(f)

ROOM_CODE = "1234"
QUESTION_DURATION = 15

clients = []
players = {}
answered_players = set()
answer_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

current_question_index = 0
quiz_started = False
question_open = False
auto_task = None


def get_current_question():
    q = questions[current_question_index]
    return {
        "question_index": current_question_index + 1,
        "question": q["question"],
        "options": q["options"]
    }


def get_correct_letter():
    idx = questions[current_question_index]["correct"]
    return ["A", "B", "C", "D"][idx]


async def broadcast(payload: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.append(ws)

    for ws in dead:
        if ws in clients:
            clients.remove(ws)


async def broadcast_leaderboard():
    await broadcast({
        "type": "leaderboard",
        "players": players
    })


async def broadcast_question():
    await broadcast({
        "type": "question",
        "data": get_current_question(),
        "duration": QUESTION_DURATION
    })


async def broadcast_answer_stats():
    await broadcast({
        "type": "answer_stats",
        "counts": answer_counts,
        "correct_answer": get_correct_letter()
    })


async def close_question():
    global question_open
    question_open = False
    await broadcast({
        "type": "question_closed",
        "correct_answer": get_correct_letter()
    })
    await broadcast_answer_stats()


async def auto_close_question():
    await asyncio.sleep(QUESTION_DURATION)
    if question_open:
        await close_question()


def reset_answer_state():
    global answered_players, answer_counts
    answered_players = set()
    answer_counts = {"A": 0, "B": 0, "C": 0, "D": 0}


@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "static/index.html"))


@app.get("/player")
def player():
    return FileResponse(os.path.join(BASE_DIR, "static/player.html"))


@app.get("/host")
def host():
    return FileResponse(os.path.join(BASE_DIR, "static/host.html"))


@app.get("/health")
def health():
    return {"ok": True}


@app.head("/")
def root_head():
    return {}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global quiz_started, question_open, current_question_index, auto_task

    await websocket.accept()
    clients.append(websocket)

    try:
        await websocket.send_text(json.dumps({
            "type": "room_info",
            "room_code": ROOM_CODE,
            "quiz_started": quiz_started,
            "question_open": question_open
        }))

        await websocket.send_text(json.dumps({
            "type": "leaderboard",
            "players": players
        }))

        if quiz_started:
            await websocket.send_text(json.dumps({
                "type": "question",
                "data": get_current_question(),
                "duration": QUESTION_DURATION
            }))

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "join":
                name = data.get("name", "").strip()
                room_code = data.get("room_code", "").strip()

                if room_code != ROOM_CODE:
                    await websocket.send_text(json.dumps({
                        "type": "info",
                        "message": "Oda kodu yanlış."
                    }))
                    continue

                if not name:
                    await websocket.send_text(json.dumps({
                        "type": "info",
                        "message": "İsim gerekli."
                    }))
                    continue

                if name not in players:
                    players[name] = 0

                await websocket.send_text(json.dumps({
                    "type": "join_success",
                    "name": name,
                    "room_code": ROOM_CODE
                }))

                await broadcast_leaderboard()

                if quiz_started:
                    await websocket.send_text(json.dumps({
                        "type": "question",
                        "data": get_current_question(),
                        "duration": QUESTION_DURATION
                    }))

            elif msg_type == "start_quiz":
                quiz_started = True
                question_open = True
                current_question_index = 0
                reset_answer_state()

                await broadcast_question()

                if auto_task and not auto_task.done():
                    auto_task.cancel()
                auto_task = asyncio.create_task(auto_close_question())

            elif msg_type == "next_question":
                if current_question_index < len(questions) - 1:
                    current_question_index += 1
                    question_open = True
                    reset_answer_state()

                    await broadcast_question()

                    if auto_task and not auto_task.done():
                        auto_task.cancel()
                    auto_task = asyncio.create_task(auto_close_question())
                else:
                    await broadcast({
                        "type": "quiz_finished"
                    })
                    await broadcast_leaderboard()

            elif msg_type == "restart_quiz":
                current_question_index = 0
                quiz_started = False
                question_open = False
                reset_answer_state()

                for player_name in players:
                    players[player_name] = 0

                await broadcast({
                    "type": "info",
                    "message": "Quiz sıfırlandı."
                })
                await broadcast_leaderboard()

            elif msg_type == "show_answer":
                if question_open:
                    await close_question()

            elif msg_type == "answer":
                if not question_open:
                    await websocket.send_text(json.dumps({
                        "type": "info",
                        "message": "Bu soru kapandı."
                    }))
                    continue

                player_name = data.get("name", "").strip()
                answer = data.get("answer", "").strip()

                if player_name not in players:
                    await websocket.send_text(json.dumps({
                        "type": "info",
                        "message": "Önce oyuna katıl."
                    }))
                    continue

                if player_name in answered_players:
                    await websocket.send_text(json.dumps({
                        "type": "info",
                        "message": "Bu soruya zaten cevap verdin."
                    }))
                    continue

                if answer not in ["A", "B", "C", "D"]:
                    continue

                answered_players.add(player_name)
                answer_counts[answer] += 1

                correct = get_correct_letter()
                is_correct = answer == correct

                if is_correct:
                    players[player_name] += 10

                await websocket.send_text(json.dumps({
                    "type": "answer_result",
                    "correct": is_correct,
                    "your_answer": answer,
                    "correct_answer": correct,
                    "score": players[player_name]
                }))

                await broadcast_leaderboard()

                await broadcast({
                    "type": "host_answer_info",
                    "player": player_name,
                    "answer": answer,
                    "correct": is_correct
                })

    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)
