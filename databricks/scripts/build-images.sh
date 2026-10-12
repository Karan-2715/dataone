#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# DataOne - Build Docker Images for Databricks Lakehouse App
# ════════════════════════════════════════════════════════════════════
#
# This script builds optimized Docker images for DataOne components
# and pushes them to a container registry for Databricks deployment.
#
# Usage:
#   ./build-images.sh [OPTIONS]
#
# Options:
#   -r, --registry REGISTRY   Container registry URL (required)
#   -t, --tag TAG            Image tag (default: latest)
#   -p, --push               Push images to registry after build
#   -h, --help               Show this help message
#
# Examples:
#   # Build only
#   ./build-images.sh -r gcr.io/my-project
#
#   # Build and push with version tag
#   ./build-images.sh -r gcr.io/my-project -t v1.0.0 -p
#
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
REGISTRY=""
TAG="latest"
PUSH=false
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Print colored message
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Show help
show_help() {
    sed -n '2,/^# ═\+$/p' "$0" | sed 's/^# \?//'
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -p|--push)
            PUSH=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Validate registry
if [[ -z "$REGISTRY" ]]; then
    error "Registry URL is required. Use -r or --registry option."
    show_help
    exit 1
fi

# Image names
BACKEND_IMAGE="${REGISTRY}/dataone-backend:${TAG}"
FRONTEND_IMAGE="${REGISTRY}/dataone-frontend:${TAG}"

log "Starting DataOne image build process..."
log "Registry: ${REGISTRY}"
log "Tag: ${TAG}"
log "Push: ${PUSH}"
echo ""

# ════════════════════════════════════════════════════════════════════
# Build Backend Image
# ════════════════════════════════════════════════════════════════════

log "Building backend image: ${BACKEND_IMAGE}"
cd "$PROJECT_ROOT"

if docker build \
    -f databricks/backend.Dockerfile \
    -t "${BACKEND_IMAGE}" \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    .; then
    success "Backend image built successfully"
else
    error "Failed to build backend image"
    exit 1
fi

echo ""

# ════════════════════════════════════════════════════════════════════
# Build Frontend Image
# ════════════════════════════════════════════════════════════════════

log "Building frontend image: ${FRONTEND_IMAGE}"

if docker build \
    -f databricks/frontend.Dockerfile \
    -t "${FRONTEND_IMAGE}" \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    .; then
    success "Frontend image built successfully"
else
    error "Failed to build frontend image"
    exit 1
fi

echo ""

# ════════════════════════════════════════════════════════════════════
# Push Images (if requested)
# ════════════════════════════════════════════════════════════════════

if [[ "$PUSH" == true ]]; then
    log "Pushing images to registry..."
    
    # Push backend
    log "Pushing backend image..."
    if docker push "${BACKEND_IMAGE}"; then
        success "Backend image pushed successfully"
    else
        error "Failed to push backend image"
        exit 1
    fi
    
    # Push frontend
    log "Pushing frontend image..."
    if docker push "${FRONTEND_IMAGE}"; then
        success "Frontend image pushed successfully"
    else
        error "Failed to push frontend image"
        exit 1
    fi
    
    echo ""
fi

# ════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════

success "Build complete!"
echo ""
echo "Images built:"
echo "  Backend:  ${BACKEND_IMAGE}"
echo "  Frontend: ${FRONTEND_IMAGE}"
echo ""

if [[ "$PUSH" == true ]]; then
    echo "Images have been pushed to the registry."
    echo ""
    echo "Next steps:"
    echo "  1. Update databricks/app.yml with these image references"
    echo "  2. Test deployment in a partner workspace"
    echo "  3. Submit to Databricks Marketplace"
else
    echo "Images are built locally. To push to registry, use -p or --push option."
fi

echo ""
log "Done!"
