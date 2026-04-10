"""
T18: Expo WebSocket Audio Client

This module provides a WebSocket client for mobile audio streaming with:
- Reconnection with exponential backoff
- JWT authentication in headers
- PCM16 audio streaming
- Heartbeat/ping handling
- Connection state management

Features:
- Automatic reconnection on disconnect
- Configurable retry attempts and backoff
- Secure JWT token handling
- Async/await for non-blocking I/O
"""

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

logger = structlog.get_logger("cassandra.mobile.audio_client")


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class AudioStreamConfig:
    """Configuration for audio stream client."""
    
    # WebSocket settings
    ws_url: str = "wss://api.cassandra.ai/ws/audio"
    org_id: str = ""
    jwt_token: str = ""
    
    # Reconnection settings
    max_reconnect_attempts: int = 10
    initial_reconnect_delay: float = 1.0  # seconds
    max_reconnect_delay: float = 60.0  # seconds
    reconnect_backoff_multiplier: float = 2.0
    
    # Audio settings
    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    
    # Heartbeat settings
    heartbeat_interval: float = 5.0  # seconds
    heartbeat_timeout: float = 10.0  # seconds
    
    # Buffer settings
    buffer_size_ms: int = 100  # Audio chunk size in ms
    max_buffer_size: int = 1024 * 1024  # 1MB max buffer


class AudioStreamClient:
    """
    WebSocket client for mobile audio streaming.
    
    Features:
    - Automatic reconnection with exponential backoff
    - JWT authentication in WebSocket headers
    - PCM16 audio streaming
    - Connection state management
    - Event callbacks for connection changes
    
    Usage:
        config = AudioStreamConfig(
            ws_url="wss://api.cassandra.ai/ws/audio/org_123",
            jwt_token="eyJhbGciOiJIUzI1NiIs...",
            org_id="org_123"
        )
        
        client = AudioStreamClient(config)
        await client.connect()
        
        # Stream audio
        await client.send_audio(audio_bytes)
        
        # Handle responses
        @client.on_message
        def handle_message(msg):
            print(f"Received: {msg}")
        
        await client.disconnect()
    """
    
    def __init__(self, config: AudioStreamConfig):
        """
        Initialize the audio stream client.
        
        Args:
            config: AudioStreamConfig with connection settings
        """
        self.config = config
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_attempts = 0
        self._last_heartbeat: float = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self._on_message_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._on_state_change_callbacks: List[Callable[[ConnectionState], None]] = []
        self._on_error_callbacks: List[Callable[[Exception], None]] = []
        
        # Audio buffer
        self._audio_buffer: bytes = b""
        
        logger.info(
            "audio_client_initialized",
            ws_url=config.ws_url,
            org_id=config.org_id,
            max_reconnect=config.max_reconnect_attempts
        )
    
    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._state == ConnectionState.CONNECTED and self._websocket is not None
    
    def _set_state(self, new_state: ConnectionState):
        """Set connection state and notify listeners."""
        old_state = self._state
        self._state = new_state
        
        if old_state != new_state:
            logger.info(
                "connection_state_changed",
                old_state=old_state.value,
                new_state=new_state.value
            )
            for callback in self._on_state_change_callbacks:
                try:
                    callback(new_state)
                except Exception as e:
                    logger.error("state_callback_error", error=str(e))
    
    def _get_reconnect_delay(self) -> float:
        """Calculate reconnection delay with exponential backoff."""
        delay = self.config.initial_reconnect_delay * (
            self.config.reconnect_backoff_multiplier ** self._reconnect_attempts
        )
        return min(delay, self.config.max_reconnect_delay)
    
    def _build_ws_url(self) -> str:
        """Build WebSocket URL with authentication."""
        base_url = self.config.ws_url.rstrip('/')
        
        # Add org_id to URL if not present
        if self.config.org_id not in base_url:
            if '/ws/audio/' not in base_url:
                base_url = f"{base_url}/{self.config.org_id}"
        
        # Add token as query parameter (for WebSocket auth)
        separator = '&' if '?' in base_url else '?'
        return f"{base_url}{separator}token={self.config.jwt_token}"
    
    async def connect(self) -> bool:
        """
        Connect to WebSocket server.
        
        Returns:
            True if connected successfully, False otherwise
        """
        if self.is_connected:
            logger.warning("already_connected")
            return True
        
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            ws_url = self._build_ws_url()
            
            # Connect with custom headers for JWT
            headers = {
                "Authorization": f"Bearer {self.config.jwt_token}",
                "X-Organization-ID": self.config.org_id,
                "X-Client-Type": "expo-mobile"
            }
            
            logger.info("connecting_to_websocket", url=ws_url.split('?')[0])
            
            self._websocket = await websockets.connect(
                ws_url,
                extra_headers=headers,
                ping_interval=self.config.heartbeat_interval,
                ping_timeout=self.config.heartbeat_timeout
            )
            
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_attempts = 0
            self._last_heartbeat = time.time()
            
            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            logger.info("websocket_connected")
            return True
            
        except InvalidStatusCode as e:
            logger.error("connection_auth_failed", status_code=e.status_code)
            self._set_state(ConnectionState.ERROR)
            return False
            
        except Exception as e:
            logger.error("connection_failed", error_type=type(e).__name__, error=str(e))
            self._set_state(ConnectionState.ERROR)
            return False
    
    async def disconnect(self):
        """Disconnect from WebSocket server."""
        logger.info("disconnecting")
        
        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        # Close websocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.warning("websocket_close_error", error=str(e))
            self._websocket = None
        
        self._set_state(ConnectionState.DISCONNECTED)
        logger.info("disconnected")
    
    async def _receive_loop(self):
        """Background task to receive messages from server."""
        while self.is_connected and self._websocket:
            try:
                message = await self._websocket.recv()
                
                # Update heartbeat timestamp
                self._last_heartbeat = time.time()
                
                # Parse JSON message
                try:
                    if isinstance(message, str):
                        data = json.loads(message)
                        await self._handle_message(data)
                    else:
                        # Binary message (shouldn't happen for control messages)
                        logger.warning("received_binary_message", size=len(message))
                except json.JSONDecodeError:
                    logger.warning("invalid_json_received", message_preview=message[:100])
                    
            except ConnectionClosed:
                logger.info("connection_closed_by_server")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("receive_error", error_type=type(e).__name__, error=str(e))
                for callback in self._on_error_callbacks:
                    try:
                        callback(e)
                    except Exception:
                        pass
        
        # Connection lost, attempt reconnection
        if self._state != ConnectionState.DISCONNECTED:
            asyncio.create_task(self._attempt_reconnect())
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming message from server."""
        msg_type = data.get("type", "unknown")
        
        if msg_type == "heartbeat":
            # Respond to server heartbeat
            logger.debug("heartbeat_received")
            
        elif msg_type == "connected":
            logger.info("server_welcome", client_id=data.get("client_id"))
            
        elif msg_type == "segment":
            logger.debug(
                "audio_segment_ack",
                segment_number=data.get("segment_number"),
                duration_ms=data.get("duration_ms")
            )
            
        elif msg_type == "error":
            logger.error("server_error", message=data.get("message"))
            
        else:
            logger.debug("message_received", type=msg_type)
        
        # Notify all registered callbacks
        for callback in self._on_message_callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error("message_callback_error", error=str(e))
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeat messages."""
        while self.is_connected:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                if not self.is_connected:
                    break
                
                # Check if we've received heartbeat recently
                time_since_last = time.time() - self._last_heartbeat
                if time_since_last > self.config.heartbeat_timeout:
                    logger.warning("heartbeat_timeout")
                    await self._websocket.close()
                    break
                
                # Send client heartbeat
                await self._send_json({
                    "type": "ping",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("heartbeat_error", error=str(e))
    
    async def _send_json(self, data: Dict[str, Any]):
        """Send JSON message to server."""
        if self._websocket and self.is_connected:
            await self._websocket.send(json.dumps(data))
    
    async def _attempt_reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        if self._reconnect_attempts >= self.config.max_reconnect_attempts:
            logger.error("max_reconnect_attempts_reached")
            self._set_state(ConnectionState.ERROR)
            return
        
        self._reconnect_attempts += 1
        delay = self._get_reconnect_delay()
        
        self._set_state(ConnectionState.RECONNECTING)
        
        logger.info(
            "attempting_reconnect",
            attempt=self._reconnect_attempts,
            max_attempts=self.config.max_reconnect_attempts,
            delay_seconds=delay
        )
        
        await asyncio.sleep(delay)
        
        success = await self.connect()
        
        if success:
            logger.info("reconnect_successful")
            self._reconnect_attempts = 0
        else:
            logger.warning("reconnect_failed")
            # Will try again if max attempts not reached
    
    async def send_audio(self, audio_data: bytes) -> bool:
        """
        Send audio data to server.
        
        Args:
            audio_data: Raw PCM16 audio bytes
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected:
            logger.warning("cannot_send_audio_not_connected")
            return False
        
        try:
            await self._websocket.send(audio_data)
            return True
        except Exception as e:
            logger.error("audio_send_error", error=str(e))
            return False
    
    async def send_control(self, action: str, **params) -> bool:
        """
        Send control message to server.
        
        Args:
            action: Control action (e.g., "reset", "status")
            **params: Additional parameters
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected:
            logger.warning("cannot_send_control_not_connected")
            return False
        
        try:
            await self._send_json({
                "action": action,
                **params
            })
            return True
        except Exception as e:
            logger.error("control_send_error", error=str(e))
            return False
    
    def on_message(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Register callback for incoming messages.
        
        Args:
            callback: Function to call with message data
            
        Returns:
            The callback (for use as decorator)
        """
        self._on_message_callbacks.append(callback)
        return callback
    
    def on_state_change(self, callback: Callable[[ConnectionState], None]):
        """
        Register callback for state changes.
        
        Args:
            callback: Function to call with new state
            
        Returns:
            The callback (for use as decorator)
        """
        self._on_state_change_callbacks.append(callback)
        return callback
    
    def on_error(self, callback: Callable[[Exception], None]):
        """
        Register callback for errors.
        
        Args:
            callback: Function to call with exception
            
        Returns:
            The callback (for use as decorator)
        """
        self._on_error_callbacks.append(callback)
        return callback
    
    def remove_callback(self, callback: Callable):
        """Remove a registered callback."""
        if callback in self._on_message_callbacks:
            self._on_message_callbacks.remove(callback)
        if callback in self._on_state_change_callbacks:
            self._on_state_change_callbacks.remove(callback)
        if callback in self._on_error_callbacks:
            self._on_error_callbacks.remove(callback)
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()


# =============================================================================
# Convenience Functions
# =============================================================================

async def stream_audio_file(
    file_path: str,
    ws_url: str,
    jwt_token: str,
    org_id: str,
    chunk_size_ms: int = 100
) -> List[Dict[str, Any]]:
    """
    Stream an audio file to the WebSocket server.
    
    Args:
        file_path: Path to PCM16 audio file
        ws_url: WebSocket URL
        jwt_token: JWT authentication token
        org_id: Organization ID
        chunk_size_ms: Chunk size in milliseconds
        
    Returns:
        List of server responses
    """
    responses = []
    
    config = AudioStreamConfig(
        ws_url=ws_url,
        jwt_token=jwt_token,
        org_id=org_id,
        buffer_size_ms=chunk_size_ms
    )
    
    client = AudioStreamClient(config)
    
    @client.on_message
    def collect_response(msg):
        responses.append(msg)
    
    async with client:
        # Read and stream audio file
        bytes_per_chunk = int(16000 * 2 * chunk_size_ms / 1000)  # 16kHz, 16-bit
        
        with open(file_path, 'rb') as f:
            while chunk := f.read(bytes_per_chunk):
                await client.send_audio(chunk)
                await asyncio.sleep(chunk_size_ms / 1000)  # Simulate real-time
    
    return responses


# =============================================================================
# React Native / Expo Integration Helper
# =============================================================================

class ExpoAudioStreamManager:
    """
    Helper class for Expo/React Native audio streaming integration.
    
    This class provides a simplified interface for React Native apps
    using expo-av for audio recording.
    
    Usage (React Native):
        const audioManager = new ExpoAudioStreamManager({
            wsUrl: 'wss://api.cassandra.ai/ws/audio',
            jwtToken: userToken,
            orgId: orgId
        });
        
        await audioManager.startRecording();
        // ... recording ...
        await audioManager.stopRecording();
    """
    
    def __init__(self, config: AudioStreamConfig):
        """Initialize Expo audio stream manager."""
        self.client = AudioStreamClient(config)
        self._recording = False
        
    async def start_streaming(self) -> bool:
        """Start audio streaming."""
        success = await self.client.connect()
        if success:
            self._recording = True
        return success
    
    async def stop_streaming(self):
        """Stop audio streaming."""
        self._recording = False
        await self.client.disconnect()
    
    async def send_audio_chunk(self, audio_data: bytes) -> bool:
        """Send audio chunk from Expo recording."""
        if not self._recording:
            return False
        return await self.client.send_audio(audio_data)
    
    def is_recording(self) -> bool:
        """Check if currently recording/streaming."""
        return self._recording and self.client.is_connected
    
    @property
    def connection_state(self) -> str:
        """Get connection state as string."""
        return self.client.state.value
