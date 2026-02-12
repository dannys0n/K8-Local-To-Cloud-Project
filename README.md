# Kubernetes Local-to-Cloud Development Environment

> A complete local Kubernetes development environment featuring monitoring, databases, and management tools, designed to mirror production infrastructure for seamless local development and testing.

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Available Services](#available-services)
- [Usage](#usage)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Contributing](#contributing)

## 🎯 Overview

This project provides a production-like Kubernetes development environment running locally using [Kind](https://kind.sigs.k8s.io/) (Kubernetes in Docker). It includes a complete observability stack, databases, and management tools - everything you need for full-stack application development with Kubernetes.

**Key Benefits:**
- 🚀 **Fast Setup**: Get a full K8s environment running in minutes
- 🔄 **Production Parity**: Mirror your production infrastructure locally
- 📊 **Full Observability**: Built-in Prometheus + Grafana monitoring
- 🗄️ **Database Ready**: PostgreSQL and Redis pre-configured
- 🎛️ **Easy Management**: Portainer UI for visual cluster management

## ✨ Features

### Kubernetes Cluster
- **Kind-based**: Lightweight, fast, and Docker-native
- **Single-node control plane**: Optimized for local development
- **Easy reset**: Destroy and recreate the cluster in seconds

### Monitoring Stack
- **Prometheus**: Metrics collection and alerting (kube-prometheus-stack)
- **Grafana**: Visualization dashboards with pre-configured datasources
- **Operator-managed**: Auto-configured for Kubernetes monitoring

### Database Services
- **PostgreSQL 16**: Production-ready relational database
  - Pre-configured with `app` database
  - Ready for application connections
  - Health checks included
- **Redis 7**: High-performance in-memory data store
  - Alpine-based for minimal footprint
  - Ready for caching and pub/sub

### Management Tools
- **Portainer**: Web-based Kubernetes management UI
  - Visual pod/deployment management
  - Log viewing and debugging
  - Resource monitoring

### Testing Service
- **HTTP Echo**: Simple test service for validating deployments

## 📦 Prerequisites

Before you begin, ensure you have the following tools installed:

### Required Tools

1. **Docker Desktop** (or Docker Engine)
   - [Download Docker Desktop](https://www.docker.com/products/docker-desktop)
   - Required for Kind to run Kubernetes nodes

2. **Kind** (Kubernetes in Docker)
   ```bash
   # macOS
   brew install kind
   
   # Linux
   curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
   chmod +x ./kind
   sudo mv ./kind /usr/local/bin/kind
   
   # Windows (with Chocolatey)
   choco install kind
   ```

3. **kubectl** (Kubernetes CLI)
   ```bash
   # macOS
   brew install kubectl
   
   # Linux
   curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
   chmod +x kubectl
   sudo mv kubectl /usr/local/bin/
   
   # Windows (with Chocolatey)
   choco install kubernetes-cli
   ```

4. **Helm** (Kubernetes Package Manager)
   ```bash
   # macOS
   brew install helm
   
   # Linux
   curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
   
   # Windows (with Chocolatey)
   choco install kubernetes-helm
   ```

### Optional Tools (for testing)
- **redis-cli**: For testing Redis connectivity
- **postgresql-client**: For testing PostgreSQL connectivity

## 🚀 Quick Start

Get up and running in 3 simple steps:

```bash
# 1. Clone the repository
git clone https://github.com/dannys0n/K8-Local-To-Cloud-Project.git
cd K8-Local-To-Cloud-Project

# 2. Deploy everything (cluster + all services)
make up

# 3. Access the services
# Grafana: http://localhost:3000
# Prometheus: http://localhost:9090
# Portainer: http://localhost:9000
```

That's it! Your complete Kubernetes development environment is ready.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│         Kind Cluster (Control Plane)            │
│                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────┐ │
│  │ Monitoring  │  │  Databases   │  │ Portainer│
│  │ Namespace   │  │  Namespace   │  │ Namespace│
│  │             │  │              │  │          │
│  │ • Prometheus│  │ • PostgreSQL │  │ • Web UI │
│  │ • Grafana   │  │ • Redis      │  │          │
│  └─────────────┘  └──────────────┘  └────────┘ │
│                                                  │
│  ┌─────────────┐                                │
│  │    Test     │                                │
│  │  Namespace  │                                │
│  │             │                                │
│  │ • Echo Svc  │                                │
│  └─────────────┘                                │
└─────────────────────────────────────────────────┘
           ↕ (port-forward)
    ┌──────────────────┐
    │   localhost      │
    │  :3000, :9090    │
    │  :9000, :5432    │
    │  :6379           │
    └──────────────────┘
```

## 🌐 Available Services

Once deployed, the following services are available on your local machine:

| Service | URL | Credentials | Description |
|---------|-----|-------------|-------------|
| **Grafana** | http://localhost:3000 | Get password¹ | Metrics visualization and dashboards |
| **Prometheus** | http://localhost:9090 | None | Metrics collection and queries |
| **Portainer** | http://localhost:9000 | Create on first visit | Kubernetes management UI |
| **PostgreSQL** | localhost:5432 | postgres/postgres | Relational database (db: `app`) |
| **Redis** | localhost:6379 | None | In-memory cache/store |

¹ **Get Grafana password:**
```bash
kubectl get secret --namespace monitoring \
  -l app.kubernetes.io/component=admin-secret \
  -o jsonpath="{.items[0].data.admin-password}" | base64 --decode
echo
```

## 💻 Usage

### Available Make Commands

The project uses a Makefile for easy orchestration:

```bash
# Full Environment
make up              # Deploy complete environment (cluster + all services + port-forwarding)
make down            # Destroy the entire environment
make status          # Check status of monitoring pods

# Individual Components
make cluster         # Create Kind cluster only
make monitors        # Install Prometheus + Grafana
make managers        # Install Portainer UI
make databases       # Deploy PostgreSQL + Redis
make port-forward    # Set up local port forwarding

# Database-specific
make redis           # Deploy Redis only
make postgres        # Deploy PostgreSQL only

# Testing
make ping-redis      # Test Redis connectivity
make ping-postgres   # Test PostgreSQL connectivity
```

### Step-by-Step Setup

If you prefer to set up components individually:

```bash
# 1. Create the Kind cluster
make cluster

# 2. Deploy monitoring stack
make monitors

# 3. Deploy management UI
make managers

# 4. Deploy databases
make databases

# 5. Set up port forwarding
make port-forward
```

### Connecting to Databases

**PostgreSQL:**
```bash
# Using psql
psql -h localhost -p 5432 -U postgres -d app
# Password: postgres

# Connection string
postgresql://postgres:postgres@localhost:5432/app
```

**Redis:**
```bash
# Using redis-cli
redis-cli -h localhost -p 6379

# Test connection
redis-cli -h localhost -p 6379 ping
# Response: PONG
```

### Accessing Dashboards

**Grafana:**
1. Navigate to http://localhost:3000
2. Username: `admin`
3. Get password using the command shown in the "Available Services" section
4. Explore pre-configured Kubernetes dashboards

**Prometheus:**
1. Navigate to http://localhost:9090
2. No authentication required
3. Query Kubernetes metrics directly

**Portainer:**
1. Navigate to http://localhost:9000
2. Create admin account on first visit
3. Select "Kubernetes via local environment" when prompted

## ⚙️ Configuration

### Cluster Configuration

The Kind cluster is configured in `src/kind/cluster.yaml`:
```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
```

### Monitoring Configuration

Customize monitoring in `src/monitoring/values.yaml`:
- Enable/disable Grafana
- Configure service types
- Enable/disable Alertmanager

### Database Configuration

**PostgreSQL** (`src/databases/postgres.yaml`):
- Image: `postgres:16-alpine`
- Default database: `app`
- Default credentials: `postgres/postgres`
- Includes readiness probe

**Redis** (`src/databases/redis.yaml`):
- Image: `redis:7-alpine`
- No authentication by default
- ClusterIP service

### Portainer Configuration

Managed via Helm values in `src/managing/portainer-values.yaml`:
- Service type: ClusterIP
- HTTP port: 9000
- Local management enabled

## 🔧 Troubleshooting

### Common Issues

**Issue: Port already in use**
```bash
# Stop existing port-forwards
./src/scripts/stop-port-forward.sh

# Or manually kill processes
kill $(lsof -ti:3000,9090,9000,5432,6379)
```

**Issue: Cluster creation fails**
```bash
# Ensure Docker is running
docker ps

# Delete existing cluster
kind delete cluster --name dev

# Recreate
make cluster
```

**Issue: Pods not starting**
```bash
# Check pod status
kubectl get pods --all-namespaces

# View pod logs
kubectl logs -n monitoring <pod-name>

# Describe pod for events
kubectl describe pod -n monitoring <pod-name>
```

**Issue: Can't access services**
```bash
# Verify port-forwarding is running
ps aux | grep "kubectl port-forward"

# Restart port-forwarding
./src/scripts/stop-port-forward.sh
make port-forward
```

### Checking Service Health

```bash
# Monitoring namespace
kubectl get pods -n monitoring

# Databases namespace
kubectl get pods -n databases

# Portainer namespace
kubectl get pods -n portainer

# All namespaces
kubectl get pods --all-namespaces
```

### Resetting the Environment

If things go wrong, completely reset:
```bash
# Full teardown
make down

# Recreate everything
make up
```

## 📁 Project Structure

```
.
├── Makefile                          # Main orchestration commands
├── README.md                         # This file
├── src/
│   ├── databases/
│   │   ├── namespace.yaml           # Databases namespace definition
│   │   ├── postgres.yaml            # PostgreSQL deployment + service
│   │   └── redis.yaml               # Redis deployment + service
│   ├── kind/
│   │   └── cluster.yaml             # Kind cluster configuration
│   ├── managing/
│   │   └── portainer-values.yaml    # Portainer Helm values
│   ├── monitoring/
│   │   └── values.yaml              # Prometheus/Grafana Helm values
│   ├── scripts/
│   │   ├── cluster.sh               # Create Kind cluster
│   │   ├── managers.sh              # Install Portainer
│   │   ├── monitors.sh              # Install monitoring stack
│   │   ├── ping-postgres.sh         # Test PostgreSQL connectivity
│   │   ├── ping-redis.sh            # Test Redis connectivity
│   │   ├── port-forward.sh          # Setup port forwarding
│   │   ├── stop-port-forward.sh     # Stop port forwarding
│   │   └── teardown.sh              # Destroy environment
│   └── test-service/
│       └── echo.yaml                # HTTP echo test service
├── docs/                            # Additional documentation
└── .github/
    └── workflows/
        ├── ci.yml                   # Continuous integration
        └── auto-readme.yml          # Automated README generation
```

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Make your changes**
4. **Test thoroughly**: Ensure all components still work
5. **Commit your changes**: `git commit -m 'Add amazing feature'`
6. **Push to the branch**: `git push origin feature/amazing-feature`
7. **Open a Pull Request**

## 📝 Notes

- **Development Focus**: This environment is optimized for local development, not production
- **Data Persistence**: Database data is stored in pod storage (ephemeral by default)
- **Resource Usage**: Monitor Docker Desktop resource allocation for optimal performance
- **Port Conflicts**: Ensure ports 3000, 9000, 9090, 5432, and 6379 are available

## 📄 License

This project is open source and available under the [MIT License](LICENSE).

## 🙏 Acknowledgments

- [Kind](https://kind.sigs.k8s.io/) - Kubernetes in Docker
- [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) - Complete monitoring stack
- [Portainer](https://www.portainer.io/) - Container management UI

---

**Made with ❤️ for Kubernetes developers**
