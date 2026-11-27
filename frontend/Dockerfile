# Build stage (Vite 7 needs Node 20.19+)
FROM node:22-alpine AS builder

WORKDIR /app

# Accept build arguments
ARG VITE_API_BASE_URL

# Expose them to the Vite build process
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the application
RUN npm run build

# Production stage
FROM node:22-alpine

WORKDIR /app

# Install a simple HTTP server to serve the static files
RUN npm install -g serve

# Copy built assets from builder
COPY --from=builder /app/dist ./dist

# Expose port 80
EXPOSE 80

# Start the server on port 80
CMD ["serve", "-s", "dist", "-l", "80"]
