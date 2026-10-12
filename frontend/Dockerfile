FROM node:22-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV BACKEND_URL=http://backend:8741
RUN npm run build
EXPOSE 43123
CMD ["npx", "next", "start", "--hostname", "0.0.0.0", "--port", "43123"]
