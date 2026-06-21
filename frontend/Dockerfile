# CS2 Decision Coach — frontend (Vite build served by nginx)

# ---- Build stage ----
FROM node:20-alpine AS build
WORKDIR /app

# VITE_BACKEND_URL is baked in at build time. Default "" => the app uses
# same-origin relative paths and nginx (below) proxies them to the backend.
ARG VITE_BACKEND_URL=""
ENV VITE_BACKEND_URL=${VITE_BACKEND_URL}

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- Serve stage ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
