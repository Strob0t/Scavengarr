# Scavengarr
graph TD
  subgraph "Unified Process"
    direction TB
    CLI[CLI] --> SR[SearchService]
    HTTP[FastAPI] --> SR
    SR --> PLM[PluginManager]
    SR --> BP[BrowserPool]
    BP --> CS[Cache Service]
    SR --> TR[TorznabRenderer]
    TR --> HTTP
  end

  graph TD
  subgraph "Coordinator (FastAPI)"
    HTTP_C[FastAPI] --> RPC_C[RPC‑Client]
    RPC_C -.->|HTTP/WS| RPC_W
    RPC_C --> EB_C[EventBus]
  end

  subgraph "Worker"
    RPC_W[RPC‑Server] --> PLM_W[PluginManager]
    RPC_W --> BP_W[BrowserPool]
    RPC_W --> CS_W[Cache Service]
    RPC_W --> SR_W[SearchService]
    SR_W --> TR_W[TorznabRenderer]
  end

  RPC_C <===> RPC_W
