"""
Trailpad webhook client for forwarding events.
"""

import requests
import hmac
import hashlib
import json
from typing import Dict, Any, Optional
from datetime import datetime

from src.utils.logger import logger


class TrailpadClient:
    """
    Client for sending events to trailpad.ai via webhooks.
    """
    
    def __init__(self, webhook_url: str, webhook_secret: Optional[str] = None):
        """
        Initialize Trailpad client.
        
        Args:
            webhook_url: Trailpad webhook endpoint URL
            webhook_secret: Secret for webhook signature verification
        """
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
    
    def _generate_signature(self, payload_body: str) -> str:
        """
        Generate webhook signature for payload verification.
        
        Args:
            payload_body: JSON payload as string
            
        Returns:
            Signature string
        """
        if not self.webhook_secret:
            return ""
        
        signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            payload_body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return f"sha256={signature}"
    
    async def send_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Send a generic event to trailpad.ai.
        
        Args:
            event_data: Event data dictionary
            
        Returns:
            True if successful, False otherwise
        """
        try:
            payload = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "source": "sourceant",
                "version": "1.0",
                "data": event_data
            }
            
            return await self._send_webhook(payload)
            
        except Exception as e:
            logger.error(f"Error sending event to trailpad.ai: {e}")
            return False
    
    async def send_health_check(self) -> bool:
        """
        Send health check event to trailpad.ai.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            payload = {
                "event_type": "health_check",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "source": "sourceant",
                "version": "1.0",
                "data": {
                    "status": "healthy",
                    "service": "trailpad_plugin"
                }
            }
            
            return await self._send_webhook(payload)
            
        except Exception as e:
            logger.error(f"Error sending health check: {e}")
            return False
    
    async def _send_webhook(self, payload: Dict[str, Any]) -> bool:
        """
        Send webhook payload to trailpad.ai.
        
        Args:
            payload: Webhook payload dictionary
            
        Returns:
            True if successful, False otherwise
        """
        try:
            payload_json = json.dumps(payload, separators=(',', ':'))
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'SourceAnt-Trailpad/1.0',
                'X-SourceAnt-Event': payload.get('data', {}).get('event_type', 'unknown'),
                'X-SourceAnt-Timestamp': payload.get('timestamp', ''),
            }
            
            # Add signature if secret is configured
            if self.webhook_secret:
                signature = self._generate_signature(payload_json)
                headers['X-SourceAnt-Signature'] = signature
            
            logger.debug(f"Sending webhook to trailpad.ai: {payload.get('data', {}).get('event_type', 'unknown')}")
            
            response = requests.post(
                self.webhook_url,
                data=payload_json,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            logger.debug(f"Successfully sent webhook: {payload.get('data', {}).get('event_type', 'unknown')}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error sending webhook to trailpad.ai: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending webhook: {e}")
            return False