import asyncio
import time
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Nexus Fleet Telemetry Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In strict production, replace "*" with your ERPNext domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TelemetryPing(BaseModel):
    driver: str
    manifest_id: str
    vehicle: str
    lat: float
    lng: float
    speed: float
    heading: float
    timestamp: int

active_fleet: Dict[str, Dict[str, Any]] = {}

DRIVER_LOGOUT_GRAVEYARD: Dict[str, float] = {}

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_fleet_state(self):
        current_time = time.time()

        stale_keys = [
            tracking_id for tracking_id, data in active_fleet.items() 
            if current_time - data.get("server_received_time", 0) > 60
        ]
        for k in stale_keys:
            del active_fleet[k]

        payload = {"fleet": active_fleet}
        
        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except RuntimeError:
                pass

manager = ConnectionManager()

@app.post("/telemetry/ping")
async def receive_ping(ping: TelemetryPing):
    """
    Receives 1Hz pings from Android devices. O(1) memory update for 0-lag.
    """
    driver = ping.driver if ping.driver else "Unknown_Driver"
    vehicle = ping.vehicle if ping.vehicle else "Idle"

    if driver in DRIVER_LOGOUT_GRAVEYARD:
        if time.time() - DRIVER_LOGOUT_GRAVEYARD[driver] < 120.0:
            return {"status": "rejected", "message": "Ping rejected. Driver recently logged out."}
        else:
            del DRIVER_LOGOUT_GRAVEYARD[driver]

    tracking_id = f"{driver}::{vehicle}"

    stale_keys = [
        k for k, v in active_fleet.items() 
        if (v.get("driver") == driver or v.get("vehicle") == vehicle) and k != tracking_id
    ]
    for k in stale_keys:
        del active_fleet[k]

    active_fleet[tracking_id] = {
        "tracking_id": tracking_id,
        "driver": driver,
        "manifest_id": ping.manifest_id,
        "vehicle": vehicle,
        "lat": ping.lat,
        "lng": ping.lng,
        "speed": ping.speed,
        "heading": ping.heading,
        "timestamp": ping.timestamp,
        "server_received_time": time.time()
    }
    return {"status": "secured"}

@app.post("/telemetry/driver-logout")
async def receive_driver_logout(payload: Dict = Body(...)):
    """
    🚨 NEW: Instantly drops the driver off the map, resets them to Offline, 
    and blacklists their dying background pings. Triggered by App Logout.
    """
    driver_email = payload.get("driver")
    if driver_email:
        keys_to_delete = [k for k, v in active_fleet.items() if v.get("driver") == driver_email]
        for k in keys_to_delete:
            del active_fleet[k]
            
        DRIVER_LOGOUT_GRAVEYARD[driver_email] = time.time()
        
        await manager.broadcast_fleet_state()
        print(f"📡 Fleet Telemetry Purged & Blacklisted: {driver_email} logged out.")
        
    return {"status": "purged"}

@app.post("/telemetry/driver-login")
async def receive_driver_login(payload: Dict = Body(...)):
    """
    🚨 NEW: Resurrects a driver instantly if they re-login within the 120s 
    graveyard window, clearing their blacklist status.
    """
    driver_email = payload.get("driver")
    if driver_email in DRIVER_LOGOUT_GRAVEYARD:
        del DRIVER_LOGOUT_GRAVEYARD[driver_email]
        print(f"🌅 Telemetry Resurrected: {driver_email} cleared from graveyard.")
    return {"status": "resurrected"}

@app.websocket("/telemetry/ws")
async def fleet_telemetry_stream(websocket: WebSocket):
    """
    Streams the aggregated fleet RAM state to the JS Dashboard at exactly 1Hz.
    """
    await manager.connect(websocket)
    try:
        while True:
            await manager.broadcast_fleet_state()
            

            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(
        "nexuslivedispatch:app", 
        host="0.0.0.0", 
        port=8001, 
        loop="uvloop", 
        workers=1,
        log_level="warning" # Suppresses standard HTTP logs to save disk space
    )