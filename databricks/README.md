# DataOne - Databricks Lakehouse App

This directory contains all files needed to deploy DataOne as a Databricks Lakehouse App and submit it to the Databricks Marketplace.

## Directory Structure

```
databricks/
├── app.yml                    # Lakehouse App manifest (services, OAuth, resources)
├── backend.Dockerfile         # Optimized backend container image
├── frontend.Dockerfile        # Optimized frontend container image
├── DEPLOYMENT_GUIDE.md       # Complete deployment and submission guide
├── README.md                 # This file
└── scripts/
    ├── build-images.sh       # Build and push Docker images
    ├── deploy.sh             # Deploy to test workspace
    └── test-deployment.sh    # Validate deployed app
```

## Quick Start

### 1. Build Images

```bash
# Build backend and frontend images
./databricks/scripts/build-images.sh \
  -r gcr.io/your-project \
  -t v1.0.0 \
  -p
```

### 2. Update Manifest

Edit `app.yml` and update image references:

```yaml
services:
  backend:
    image: gcr.io/your-project/dataone-backend:v1.0.0
  frontend:
    image: gcr.io/your-project/dataone-frontend:v1.0.0
```

### 3. Deploy to Test Workspace

```bash
# Upload manifest to workspace
./databricks/scripts/deploy.sh \
  -w https://your-workspace.cloud.databricks.com \
  -n dataone-test
```

### 4. Test Deployment

```bash
# Run automated validation
./databricks/scripts/test-deployment.sh \
  -u https://your-workspace.cloud.databricks.com/apps/dataone-test-123 \
  -v
```

### 5. Submit to Marketplace

1. Go to [Databricks Partner Portal](https://partner.databricks.com)
2. Upload `app.yml`, `../marketplace/listing.json`, and documentation
3. Submit for review

See [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) for complete instructions.

## Key Features

### OAuth2 Authentication
- Native Databricks OAuth with PKCE flow
- Automatic token refresh
- Unity Catalog permission inheritance

### Unity Catalog Integration
- Delegates catalog, lineage, and governance to Unity Catalog
- Respects user-level permissions (catalogs, schemas, tables)
- Automatic PII classification via UC tags

### Multi-Source Support
- Maintains multi-source connectivity (PostgreSQL, MySQL, Snowflake, etc.)
- Follows 4R strategy: Retire/Delegate/Adapt/Keep
- External connections optional - UC-only mode supported

### Enterprise-Ready
- Horizontal scaling (up to 5 backend replicas, 10 workers)
- High availability with health checks
- Encrypted secrets management
- Comprehensive audit trail

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Databricks Workspace                     │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │                  DataOne Lakehouse App                │ │
│  │                                                        │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐           │ │
│  │  │ Frontend │  │ Backend  │  │  Worker  │           │ │
│  │  │  (nginx) │◄─┤ (FastAPI)│◄─┤ (Celery) │           │ │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘           │ │
│  │       │             │              │                  │ │
│  │       └─────────────┼──────────────┘                  │ │
│  │                     │                                 │ │
│  │              ┌──────┴──────┐                          │ │
│  │              │             │                          │ │
│  │         ┌────▼────┐   ┌───▼───┐                      │ │
│  │         │PostgreSQL│   │ Redis │                      │ │
│  │         └─────────┘   └───────┘                      │ │
│  └───────────────────────┬────────────────────────────┬─┘ │
│                          │                            │   │
│                    ┌─────▼─────┐              ┌──────▼───┐ │
│                    │   Unity   │              │   SQL    │ │
│                    │  Catalog  │              │Warehouse │ │
│                    └───────────┘              └──────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

### Environment Variables

Key settings in `app.yml`:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABRICKS_NATIVE_MODE` | Enable OAuth mode | `true` |
| `ENABLE_UNITY_CATALOG` | Enable UC delegation | `true` |
| `ENABLE_EXTERNAL_CONNECTORS` | Allow non-DB sources | `true` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `MAX_QUERY_ROWS` | Max query result size | `10000` |
| `SESSION_TIMEOUT_HOURS` | User session timeout | `24` |

### Resource Limits

Default allocations:

- **Backend**: 2 CPU, 4GB RAM (scales 1-5 replicas)
- **Worker**: 2 CPU, 4GB RAM (scales 1-10 replicas)
- **Frontend**: 1 CPU, 1GB RAM (scales 1-3 replicas)
- **PostgreSQL**: 1 CPU, 2GB RAM, 10GB storage
- **Redis**: 0.5 CPU, 512MB RAM, 1GB storage

Adjust in `app.yml` under `services.<service>.resources`.

### Scaling Policies

Automatic scaling based on:
- **Backend**: CPU >70% or Memory >80%
- **Worker**: Queue depth >100 jobs
- **Frontend**: CPU >70%

Configure in `app.yml` under `scaling`.

## Security

### Data Handling
- **No external APIs**: All data stays in workspace
- **Encryption at rest**: AES-256 for PostgreSQL volumes
- **Encryption in transit**: TLS 1.3 for all connections
- **Credential encryption**: Double-encrypted (workspace + app-level)

### OAuth & RBAC
- **Authentication**: Databricks OAuth 2.0 with PKCE
- **Authorization**: Unity Catalog permission inheritance
- **Token management**: Auto-refresh with 5-minute buffer
- **Session security**: HTTP-only cookies, CSRF protection

### Compliance
- **GDPR**: Compliant (data export, deletion supported)
- **SOC 2 Type II**: In progress
- **Audit trail**: All user actions logged

See `../marketplace/SECURITY.md` for complete details.

## Testing

### Unit Tests
```bash
cd backend
pytest tests/ -v
```

### Integration Tests
```bash
# Requires running services
docker-compose up -d
pytest tests/integration/ -v
```

### Deployment Tests
```bash
./databricks/scripts/test-deployment.sh \
  -u <app-url> \
  -v
```

## Troubleshooting

### Common Issues

**Images won't build**
```bash
# Check Docker is running
docker info

# Free up space
docker system prune -a
```

**App won't start in workspace**
```bash
# Check logs
databricks apps logs dataone-test --profile DEFAULT

# Verify Unity Catalog
databricks catalogs list --profile DEFAULT
```

**OAuth errors**
```bash
# Verify callback URL
echo "https://<workspace>/apps/<app-id>/api/v1/auth/databricks/callback"

# Check client credentials in app config
databricks apps config dataone-test --profile DEFAULT
```

**Performance issues**
```bash
# Scale up workers
databricks apps scale dataone-test --service worker --replicas 5

# Check resource usage
databricks apps metrics dataone-test
```

## Support

- **Documentation**: https://docs.dataone.app
- **Email**: support@veltris.com
- **Issues**: GitHub Issues (for development)
- **Slack**: #dataone-support (Databricks Partner Slack)

## Contributing

See [../CONTRIBUTING.md](../CONTRIBUTING.md) for development guidelines.

## License

Proprietary - See [../LICENSE](../LICENSE)

---

**For detailed deployment instructions, see [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)**
