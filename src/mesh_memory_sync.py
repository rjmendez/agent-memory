#!/usr/bin/env python3
"""
Mesh Memory Sync — Bidirectional A2A Redis sync for agent memory.
Listens on mesh:memory:sync for upsert/delete operations signed by other agents.
Broadcasts local changes to mesh:memory:sync.
"""

import redis
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import base64
import uuid
from typing import Dict, Any

DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,
    'database': 'mrpink_memory',
    'user': 'mrpink',
    'password': 'MrPink-Memory-Secure-2026'
}

REDIS_CONFIG = {
    'host': 'localhost',
    'port': 6379,
    'db': 0,
    'decode_responses': True
}

class MeshMemorySync:
    def __init__(self, agent_name='MrPink', private_key_path=None):
        self.agent_name = agent_name
        self.redis = redis.Redis(**REDIS_CONFIG)
        self.db = psycopg2.connect(**DB_CONFIG)
        self.private_key_path = private_key_path
        self.private_key = None
        
        if private_key_path:
            self._load_private_key()
    
    def _load_private_key(self):
        """Load RSA private key for signing."""
        try:
            with open(self.private_key_path, 'rb') as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None
                )
        except Exception as e:
            print(f"Warning: Could not load private key: {e}")
    
    def sign_message(self, data: Dict[str, Any]) -> str:
        """Sign a message with RSA-2048."""
        if not self.private_key:
            return None
        
        json_str = json.dumps(data, sort_keys=True)
        signature = self.private_key.sign(
            json_str.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode()
    
    def broadcast_change(self, table_name: str, record_id: str, operation: str, payload: Dict):
        """Broadcast a local change to mesh:memory:sync."""
        
        message = {
            'op': operation,  # upsert, delete
            'table': table_name,
            'record_id': record_id,
            'owner': self.agent_name,
            'payload': payload,
            'timestamp': datetime.now().isoformat(),
            'signature': self.sign_message(payload)
        }
        
        # Publish to Redis stream
        self.redis.xadd('mesh:memory:sync', message)
        
        # Log to changelog
        with self.db.cursor() as cur:
            cur.execute("""
                INSERT INTO changelog (table_name, record_id, operation, owner, data_after, signature, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (table_name, record_id, operation, self.agent_name, json.dumps(payload), message['signature']))
        self.db.commit()
        
        print(f"📡 Broadcast: {operation} {table_name} {record_id} → mesh:memory:sync")
    
    def listen_for_sync(self, consumer_group='agents', block_ms=1000):
        """Listen for sync messages from other agents."""
        
        # Create consumer group if needed
        try:
            self.redis.xgroup_create('mesh:memory:sync', consumer_group, id='0', mkstream=True)
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise
        
        print(f"🔊 Listening on mesh:memory:sync (consumer: {self.agent_name})...")
        
        while True:
            try:
                # Read from stream
                messages = self.redis.xreadgroup(
                    groupname=consumer_group,
                    consumername=self.agent_name,
                    streams={'mesh:memory:sync': '>'},
                    block=block_ms,
                    count=10
                )
                
                if not messages:
                    continue
                
                for stream, msgs in messages:
                    for msg_id, msg_data in msgs:
                        self._handle_sync_message(msg_data, stream, consumer_group, msg_id)
            
            except KeyboardInterrupt:
                print("\n⏹️  Stopped listening")
                break
            except Exception as e:
                print(f"Error in sync loop: {e}")
    
    def _handle_sync_message(self, msg_data: Dict, stream: str, group: str, msg_id: str):
        """Process a sync message from another agent."""
        
        try:
            op = msg_data.get('op')
            table = msg_data.get('table')
            record_id = msg_data.get('record_id')
            owner = msg_data.get('owner')
            payload = msg_data.get('payload')
            
            if owner == self.agent_name:
                # Skip our own messages
                self.redis.xack(stream, group, msg_id)
                return
            
            print(f"📥 Incoming: {op} {table} {record_id} from @{owner}")
            
            # Apply to local DB
            if op == 'upsert':
                self._apply_upsert(table, record_id, payload, owner)
            elif op == 'delete':
                self._apply_delete(table, record_id, owner)
            
            # Acknowledge receipt
            self.redis.xack(stream, group, msg_id)
        
        except Exception as e:
            print(f"Error handling sync message: {e}")
    
    def _apply_upsert(self, table: str, record_id: str, payload: Dict, owner: str):
        """Apply an upsert from a remote agent."""
        
        with self.db.cursor() as cur:
            # Generic upsert pattern
            if table == 'facts':
                cur.execute("""
                    INSERT INTO facts (id, key, value, type, confidence, owner)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        confidence = EXCLUDED.confidence
                """, (record_id, payload.get('key'), payload.get('value'), payload.get('type'),
                      payload.get('confidence', 1.0), owner))
            elif table == 'tasks':
                cur.execute("""
                    INSERT INTO tasks (id, title, status, owner, priority)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        priority = EXCLUDED.priority
                """, (record_id, payload.get('title'), payload.get('status'),
                      owner, payload.get('priority', 'normal')))
            # ... more table handlers as needed
        
        self.db.commit()
        print(f"   ✅ Applied {table}:{record_id}")
    
    def _apply_delete(self, table: str, record_id: str, owner: str):
        """Apply a delete from a remote agent."""
        # Archive instead of hard-delete
        print(f"   🗑️  Would delete {table}:{record_id} (archived by {owner})")
    
    def close(self):
        self.redis.close()
        self.db.close()

def main():
    sync = MeshMemorySync(agent_name='MrPink')
    
    try:
        # Example: broadcast a task status change
        # sync.broadcast_change('tasks', str(uuid.uuid4()), 'upsert', {
        #     'title': 'Example Task',
        #     'status': 'running',
        #     'priority': 'high'
        # })
        
        # Listen for incoming changes
        sync.listen_for_sync()
    
    finally:
        sync.close()

if __name__ == '__main__':
    main()
