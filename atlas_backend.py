import json
from pathlib import Path
from typing import List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import uvicorn
from agent_memory import open_memory
from contextlib import closing

app = FastAPI(title="Talos Neural Atlas API")

# Global state for the Atlas
class AtlasState:
    def __init__(self):
        self.focus_node = None
        self.connections = []

state = AtlasState()

class Node(BaseModel):
    id: str
    label: str
    type: str  # 'memory', 'goal', 'critique', 'fact'
    val: float = 1.0
    color: str = "#ffffff"

class Link(BaseModel):
    source: str
    target: str
    label: str = ""

class GraphData(BaseModel):
    nodes: List[Node]
    links: List[Link]

def fetch_graph_data(db_path: Path) -> GraphData:
    nodes = []
    links = []

    with closing(open_memory(db_path)) as db:
        # 1. Extract Goals
        goals = db.execute("SELECT id, outcome FROM goals").fetchall()
        for g in goals:
            nodes.append(Node(id=g[0], label=g[1], type="goal", color="#ffd700"))

        # 2. Extract Commitments
        commits = db.execute("SELECT id, commitment FROM commitments").fetchall()
        for c in commits:
            nodes.append(Node(id=str(c[0]), label=c[1], type="commitment", color="#00ffcc"))

        # 3. Extract Personal Facts
        facts = db.execute("SELECT rowid, subject, value FROM personal_facts").fetchall()
        for f in facts:
            nodes.append(Node(id=f"fact_{f[0]}", label=f"{f[1]}: {f[2]}", type="fact", color="#ff00ff"))

        # 4. Extract Turn Critiques
        critiques = db.execute("SELECT rowid, reason FROM turn_critiques").fetchall()
        for cr in critiques:
            nodes.append(Node(id=f"crit_{cr[0]}", label=cr[1], type="critique", color="#ff4444"))

        # 5. Heuristic Linking: Connect everything to the "Core" or by keyword overlap
        # In a real implementation, we'd use semantic embeddings here.
        # For now, we create basic clusters.
        for i in range(1, len(nodes)):
            links.append(Link(source=nodes[0].id, target=nodes[i].id, label="related"))

    return GraphData(nodes=nodes, links=links)

@app.get("/graph")
async def get_graph():
    # Assuming default path for now, will be configurable
    db_path = Path("/Users/james/Developer/Talos/state/agent.sqlite3")
    return fetch_graph_data(db_path)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "focus_node":
                state.focus_node = msg.get("node_id")
                # Notify other clients if any
                await websocket.send_json({"type": "node_focused", "node_id": state.focus_node})
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
