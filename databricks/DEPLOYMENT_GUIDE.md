# DataOne Databricks Marketplace Deployment Guide

## Overview

This guide walks through deploying DataOne as a Databricks Lakehouse App and submitting it to the Databricks Marketplace.

## Prerequisites

### 1. Databricks Requirements

- **Databricks Account**: Partner Connect access or Marketplace Partner status
- **Test Workspace**: Unity Catalog enabled, Databricks Runtime 13.0+
- **SQL Warehouse**: At least one active SQL Warehouse
- **Permissions**: Workspace Admin role

### 2. Development Tools

```bash
# Install Databricks CLI
pip install databricks-cli

# Configure authentication
databricks configure --token
# Enter workspace URL: https://your-workspace.cloud.databricks.com
# Enter personal access token: dapi...

# Verify configuration
databricks workspace ls /
```

### 3. Container Registry

Choose a registry for hosting Docker images:

- **Google Container Registry (GCR)**: `gcr.io/your-project`
- **Amazon ECR**: `aws_account_id.dkr.ecr.region.amazonaws.com`
- **Azure Container Registry**: `yourregistry.azurecr.io`
- **Docker Hub**: `docker.io/your-username` (not recommended for production)

Authenticate with your registry:

```bash
# GCR
gcloud auth configure-docker

# ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com

# ACR
az acr login --name yourregistry
```

## Deployment Process

### Phase 1: Build and Push Images

#### Step 1: Build Docker Images

```bash
cd /path/to/DataOne-main

# Build images (backend + frontend)
./databricks/scripts/build-images.sh -r gcr.io/your-project -t v1.0.0

# This creates:
# - gcr.io/your-project/dataone-backend:v1.0.0
# - gcr.io/your-project/dataone-frontend:v1.0.0
```

#### Step 2: Test Images Locally (Optional)

```bash
# Test backend
docker run -it --rm \
  -e DATABASE_URL=postgresql://postgres:postgres@host.docker.internal:5432/dataone \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  -p 8000:8000 \
  gcr.io/your-project/dataone-backend:v1.0.0

# Test frontend
docker run -it --rm \
  -e NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 \
  -p 3000:3000 \
  gcr.io/your-project/dataone-frontend:v1.0.0
```

#### Step 3: Push to Registry

```bash
# Push images
./databricks/scripts/build-images.sh -r gcr.io/your-project -t v1.0.0 -p

# Verify images are accessible
docker pull gcr.io/your-project/dataone-backend:v1.0.0
docker pull gcr.io/your-project/dataone-frontend:v1.0.0
```

### Phase 2: Update App Manifest

#### Step 1: Update Image References

Edit `databricks/app.yml` and update the image references:

```yaml
services:
  backend:
    image: gcr.io/your-project/dataone-backend:v1.0.0  # Update this
    # ... rest of config
  
  frontend:
    image: gcr.io/your-project/dataone-frontend:v1.0.0  # Update this
    # ... rest of config
```

**Note**: PostgreSQL and Redis use public images, no changes needed.

#### Step 2: Configure Registry Access (if private)

If using a private registry, add credentials to the manifest:

```yaml
registry:
  url: gcr.io
  auth:
    username: _json_key
    password: ${SECRET_REGISTRY_PASSWORD}

secrets:
  - name: SECRET_REGISTRY_PASSWORD
    description: "Container registry credentials"
    required: true
    generate: false
```

### Phase 3: Deploy to Test Workspace

#### Step 1: Request Partner Access

1. Go to [Databricks Partner Portal](https://partner.databricks.com)
2. Sign in with your Databricks account
3. Navigate to **Partner Connect** → **Marketplace**
4. Request **Lakehouse App Developer** access
5. Wait for approval (typically 1-3 business days)

#### Step 2: Create Test Deployment

Once approved, deploy to your test workspace:

**Option A: Manual Deployment (Recommended)**

1. Navigate to your Databricks workspace
2. Go to **Workspace** → **Apps**
3. Click **Create App**
4. Select **Upload Manifest**
5. Upload `databricks/app.yml`
6. Configure settings:
   - **App Name**: dataone-test
   - **Environment**: test
   - **Log Level**: DEBUG
7. Configure OAuth:
   - Client ID: (auto-generated)
   - Client Secret: (auto-generated)
   - Copy the callback URL
8. Click **Deploy**

Wait 5-10 minutes for deployment to complete.

**Option B: CLI Deployment (Advanced)**

```bash
# Upload manifest
./databricks/scripts/deploy.sh \
  -w https://your-workspace.cloud.databricks.com \
  -p DEFAULT \
  -n dataone-test

# Follow manual steps in output
```

#### Step 3: Configure OAuth

After deployment:

1. Go to **Apps** → **dataone-test** → **Settings** → **OAuth**
2. Note your **Client ID** and **Client Secret**
3. Update environment variables:
   ```bash
   DATABRICKS_OAUTH_CLIENT_ID=<client-id>
   DATABRICKS_OAUTH_CLIENT_SECRET=<client-secret>
   ```
4. Restart the app for changes to take effect

### Phase 4: Test the Deployment

#### Step 1: Automated Tests

```bash
# Run deployment validation
./databricks/scripts/test-deployment.sh \
  -u https://your-workspace.cloud.databricks.com/apps/dataone-test-123 \
  -v

# Expected output:
# [PASS] Frontend homepage loads
# [PASS] Frontend health check
# [PASS] Backend health endpoint
# [PASS] Backend version endpoint
# [PASS] OAuth status endpoint
# ... (more tests)
# All tests passed! Deployment is ready for marketplace submission.
```

#### Step 2: Manual Testing

1. **Access the App**:
   - Navigate to the app URL
   - Should redirect to login page

2. **Test OAuth Login**:
   - Click **Sign in with Databricks**
   - Authorize the app
   - Should land on dashboard

3. **Test Unity Catalog Integration**:
   - Go to **Connections**
   - Look for auto-created Unity Catalog connection
   - Click **Browse**
   - Verify you see your catalogs/schemas/tables

4. **Test External Connection** (Optional):
   - Go to **Connections** → **New Connection**
   - Select PostgreSQL
   - Enter test database credentials
   - Click **Test Connection**
   - Should show "Connected"

5. **Test Schema Mapping**:
   - Go to **Schema Mapper**
   - Select source and target schemas
   - Click **Generate Suggestions**
   - Verify AI suggestions appear with confidence scores

6. **Test Query Studio**:
   - Go to **Query Studio**
   - Select a data source
   - Execute: `SELECT * FROM catalog.schema.table LIMIT 10`
   - Verify results appear

#### Step 3: Performance Testing

```bash
# Load testing with Apache Bench
ab -n 1000 -c 10 https://your-workspace.cloud.databricks.com/apps/dataone-test/

# Monitor resource usage
databricks apps metrics dataone-test --profile DEFAULT
```

Expected performance:
- Frontend response time: < 2s
- API response time: < 500ms
- Query execution: Depends on data size and warehouse

### Phase 5: Marketplace Submission

#### Step 1: Prepare Submission Package

Ensure you have:

- ✅ `databricks/app.yml` - App manifest
- ✅ `marketplace/listing.json` - Marketplace metadata
- ✅ `marketplace/INSTALLATION.md` - Installation guide
- ✅ `marketplace/SECURITY.md` - Security documentation
- ✅ Screenshots (4+ required):
  - Dashboard view
  - Schema mapper with AI suggestions
  - Query studio
  - Unity Catalog integration
- ✅ Demo video (optional but recommended)
- ✅ Logo (PNG, 512x512px)

#### Step 2: Create Screenshots

Capture high-quality screenshots:

```bash
# Recommended resolution: 1920x1080
# Format: PNG
# Naming: descriptive-name.png

mkdir -p marketplace/screenshots

# Example screenshots to capture:
# 1. dashboard.png - Main dashboard with connections
# 2. schema-mapper.png - Schema mapping with AI suggestions
# 3. query-studio.png - Query execution with results
# 4. unity-catalog-integration.png - UC metadata browsing
```

#### Step 3: Submit to Partner Portal

1. Go to [Databricks Partner Portal](https://partner.databricks.com)
2. Navigate to **Marketplace** → **Submit Listing**
3. Click **New Listing**
4. Fill in the form:

   **Basic Information**:
   - App Name: DataOne
   - Category: Data Integration & ETL
   - Short Description: (from listing.json)
   - Long Description: (from listing.json)

   **Technical Details**:
   - App Type: Lakehouse App
   - Manifest: Upload `databricks/app.yml`
   - Container Images: Public or provide registry credentials

   **Documentation**:
   - Installation Guide: Upload `marketplace/INSTALLATION.md`
   - Security Documentation: Upload `marketplace/SECURITY.md`
   - User Guide: Link to docs.dataone.app

   **Media**:
   - Logo: Upload logo.png
   - Screenshots: Upload all screenshots
   - Demo Video: YouTube/Vimeo link (optional)

   **Pricing**:
   - Model: Free Trial (30 days)
   - Plans: Copy from listing.json

   **Support**:
   - Email: support@veltris.com
   - Documentation: https://docs.dataone.app
   - Community: https://community.dataone.app

5. Click **Submit for Review**

#### Step 4: Review Process

**Timeline**: 2-4 weeks

**Stages**:

1. **Technical Review** (3-5 days):
   - Manifest validation
   - Security scan
   - Performance testing
   - Unity Catalog integration verification

2. **Documentation Review** (2-3 days):
   - Installation guide completeness
   - Security documentation accuracy
   - User guide clarity

3. **Business Review** (1-2 weeks):
   - Pricing model approval
   - Support plan validation
   - Legal/compliance check

4. **Final Approval**:
   - Listing published to marketplace
   - Email notification sent

**Common Rejection Reasons**:
- Incomplete security documentation
- Missing required screenshots
- Performance issues (slow load times)
- Inadequate error handling
- Missing OAuth implementation
- Insufficient Unity Catalog integration

### Phase 6: Post-Submission

#### Monitor Metrics

After marketplace approval:

```bash
# View installation metrics
databricks marketplace analytics dataone --profile DEFAULT

# Monitor customer feedback
databricks marketplace reviews dataone --profile DEFAULT
```

#### Handle Customer Issues

1. **Support Channel**: Monitor support@veltris.com
2. **Common Issues**:
   - OAuth configuration problems
   - Connection timeouts
   - Performance on large datasets
3. **Response SLA**:
   - Professional: 24 hours
   - Enterprise: 4 hours

#### Release Updates

To release a new version:

```bash
# 1. Build new images
./databricks/scripts/build-images.sh -r gcr.io/your-project -t v1.1.0 -p

# 2. Update app.yml with new tag
sed -i 's/:v1.0.0/:v1.1.0/g' databricks/app.yml

# 3. Test in staging workspace
./databricks/scripts/test-deployment.sh -u <staging-url>

# 4. Submit update to Partner Portal
# Go to Partner Portal → Your Listings → DataOne → New Version
```

## Troubleshooting

### Build Issues

**Problem**: Docker build fails

**Solutions**:
- Check Docker daemon is running
- Verify Dockerfile paths are correct
- Ensure sufficient disk space (10GB+)
- Review build logs for specific errors

### Deployment Issues

**Problem**: App won't start in workspace

**Solutions**:
- Check resource quotas (CPU, memory)
- Verify Unity Catalog is enabled
- Review app logs: `databricks apps logs dataone-test`
- Check secrets are configured correctly

### OAuth Issues

**Problem**: OAuth redirect loops

**Solutions**:
- Verify callback URL matches exactly
- Check client ID and secret are correct
- Ensure user has Unity Catalog permissions
- Clear browser cookies

### Performance Issues

**Problem**: Slow response times

**Solutions**:
- Scale up replicas in app.yml
- Enable Redis caching (check REDIS_URL)
- Use Serverless SQL Warehouses
- Optimize database queries

## Support

For deployment assistance:

- **Email**: support@veltris.com
- **Slack**: #dataone-deployment (Databricks Partner Slack)
- **Documentation**: https://docs.dataone.app/deployment

## Next Steps

After successful deployment:

1. **Marketing**: Create blog post announcing marketplace availability
2. **Customer Success**: Onboard first customers with white-glove support
3. **Iterate**: Gather feedback and plan v1.1 features
4. **Scale**: Monitor usage and scale infrastructure accordingly

---

**Good luck with your marketplace submission!** 🚀
