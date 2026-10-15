# Proxy Crack — System Graph

Component relationships, data flow, and entity model. Keep in sync with `architecture.md`.

**Last updated:** 2026-10-15

---

## 1. Component graph

```mermaid
graph TB
    subgraph Device["📱 Android Device"]
        OtherApps["All other apps<br/>(browser, social, games)"]
        subgraph OurApp["Proxy Crack"]
            UI["Flutter UI<br/>lib/features/"]
            RouteIface["core/routing/<br/>ENGINE INTERFACE"]
            VpnSvc["VpnConnectionService.kt<br/>extends VpnService"]
            Engine["sing-box / Xray<br/>gomobile .aar"]
        end
    end

    subgraph Cloud["☁️ Backend"]
        API["Django REST API<br/>JWT auth"]
        Admin["Django Admin<br/>users · subs · proxy upload"]
        DB[("PostgreSQL")]
        Health["Proxy health checker<br/>periodic task"]
    end

    subgraph External["🌐 External"]
        Providers["Paid proxy providers"]
        Play["Google Play Billing"]
        Target["Target internet hosts"]
    end

    OtherApps -->|"IP packets"| VpnSvc
    UI --> RouteIface
    RouteIface --> VpnSvc
    VpnSvc -->|"TUN fd"| Engine
    Engine -->|"chain"| Target

    UI <-->|"HTTPS"| API
    API --> DB
    Admin --> DB
    Health --> DB
    Health -.->|"probe"| Providers
    Admin -.->|"CSV upload"| DB
    UI <-->|"purchase"| Play
    API <-->|"receipt validation"| Play

    style OurApp fill:#1e293b,stroke:#38bdf8,color:#e2e8f0
    style Cloud fill:#1e293b,stroke:#4ade80,color:#e2e8f0
    style External fill:#1e293b,stroke:#fbbf24,color:#e2e8f0
    style Engine fill:#7f1d1d,stroke:#ef4444,color:#fee2e2
```

> The red node is the highest-risk component — see `architecture.md` §1.

---

## 2. Traffic path (the core mechanism)

```mermaid
sequenceDiagram
    participant App as Other app
    participant Vpn as VpnService
    participant Eng as sing-box engine
    participant H1 as Hop 1 (Shadowsocks)
    participant H2 as Hop 2 (SOCKS5)
    participant H3 as Hop 3 (HTTP)
    participant T as Target host

    App->>Vpn: raw IP packet
    Vpn->>Eng: TUN fd
    Note over Eng: userspace TCP/IP<br/>reassembles into a stream
    Eng->>H1: encrypted SS tunnel
    H1->>H2: CONNECT (detour: hop1)
    H2->>H3: CONNECT (detour: hop2)
    H3->>T: CONNECT (detour: hop3)
    T-->>App: response back down the chain

    Note over H1,H3: each hop adds a full RTT<br/>3 hops ≈ 400-900 ms
```

**Failure mode:** any hop dying kills the chain. Per-hop health checks and failover are
required, not optional.

---

## 3. Data model

```mermaid
erDiagram
    USER ||--o| SUBSCRIPTION : has
    USER ||--o{ PROXYCHAIN : saves
    USER ||--o{ USAGELOG : generates
    PLAN ||--o{ SUBSCRIPTION : defines
    PROXYCHAIN }o--|| CHAINHOP : "ordered hops"
    CHAINHOP }o--|| PROXY : references
    UPLOADBATCH ||--o{ PROXY : creates
    USER ||--o{ UPLOADBATCH : "admin uploads"

    USER {
        int id PK
        string email UK
        string password_hash
        bool is_staff
        datetime date_joined
    }
    PLAN {
        int id PK
        string name
        decimal price
        int duration_days
        int max_chain_length "free tier = 1"
        int max_devices
        bigint bandwidth_limit_bytes
        bool is_free_tier
    }
    SUBSCRIPTION {
        int id PK
        int user_id FK
        int plan_id FK
        datetime started_at
        datetime expires_at
        string status "active|expired|cancelled"
        string platform_txn_id "Play purchase token"
    }
    PROXY {
        int id PK
        string protocol "ss|socks5|http"
        string host
        int port
        string username "encrypted"
        string password "encrypted"
        string country
        string city
        string tier "free|premium"
        bool is_alive
        int latency_ms
        datetime last_checked
        int upload_batch_id FK
    }
    PROXYCHAIN {
        int id PK
        int user_id FK
        string name
        datetime created_at
    }
    CHAINHOP {
        int id PK
        int chain_id FK
        int proxy_id FK
        int position "0,1,2 - order matters"
    }
    UPLOADBATCH {
        int id PK
        int uploaded_by FK
        string filename
        int row_count
        int success_count
        int fail_count
        datetime created_at
    }
    USAGELOG {
        int id PK
        int user_id FK
        bigint bytes_up
        bigint bytes_down
        datetime session_start
        datetime session_end
    }
    CONTENT {
        int id PK
        string key UK
        string title
        text body
        bool is_published
    }
```

---

## 4. Entitlement flow — the thing that must not be client-side

```mermaid
flowchart TD
    A["User opens proxy list"] --> B["GET /api/proxies/"]
    B --> C{"JWT valid?"}
    C -->|no| D["401"]
    C -->|yes| E["Load Subscription"]
    E --> F{"Active?"}
    F -->|no| G["Fall back to free tier"]
    F -->|yes| H["Load Plan limits"]
    G --> I["Filter: tier=free<br/>max_chain_length=1"]
    H --> J["Filter by plan tier<br/>+ max_chain_length"]
    I --> K["Paginate + rate-limit"]
    J --> K
    K --> L["Return scoped proxy list"]

    style D fill:#7f1d1d,stroke:#ef4444,color:#fee2e2
    style K fill:#14532d,stroke:#4ade80,color:#dcfce7
```

**Rule:** the server decides what a user may see. The client never filters for entitlement —
it only renders what it is given. Chain length is re-validated on `POST /api/chains/`.

---

## 5. Repository layout

```
D:\project\Proxy\
├── Project\              decisions + design (this folder)
│   ├── architecture.md   system design
│   ├── memory.md         decisions, state, gotchas
│   └── graph.md          this file
├── Pre-Flight\           environment setup checklist + install log
├── app\                  Flutter mobile app
│   ├── lib\
│   │   ├── core\routing\     engine interface (swap-proof)
│   │   ├── features\         proxies · subscription · connection
│   │   └── data\             API client, cache
│   └── android\app\src\main\kotlin\
│       ├── VpnConnectionService.kt
│       └── SingBoxBridge.kt
└── backend\              Django 5 + DRF
    ├── .venv\
    ├── api\              DRF serializers, viewsets
    ├── proxies\          Proxy, UploadBatch, health checks
    ├── billing\          Plan, Subscription, Play validation
    └── core\             settings, admin customisation
```

---

## 6. Build dependency order

```mermaid
graph LR
    P1["Phase 1<br/>Django models<br/>+ admin + API"] --> P2["Phase 2<br/>Flutter UI<br/>mock engine"]
    P1 --> P5["Phase 5<br/>Play Billing<br/>+ validation"]
    P2 --> P3["Phase 3<br/>VpnService<br/>+ sing-box"]
    P3 --> P4["Phase 4<br/>Chaining<br/>+ failover"]
    P4 --> P6["Phase 6<br/>Play review<br/>+ launch"]
    P5 --> P6

    style P3 fill:#7f1d1d,stroke:#ef4444,color:#fee2e2
    style P4 fill:#78350f,stroke:#fbbf24,color:#fef3c7
```

Phases 1, 2 and 5 are conventional. **Phase 3 is the risk** and gates everything after it.
