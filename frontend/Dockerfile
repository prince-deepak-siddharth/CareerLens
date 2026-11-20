FROM node:22-alpine AS dependencies
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
ARG CAREERLENS_API_ORIGIN=http://api:8000
ARG NEXT_PUBLIC_LANGFUSE_URL=https://cloud.langfuse.com
ENV CAREERLENS_API_ORIGIN=$CAREERLENS_API_ORIGIN NEXT_PUBLIC_LANGFUSE_URL=$NEXT_PUBLIC_LANGFUSE_URL
COPY --from=dependencies /web/node_modules ./node_modules
COPY frontend/ .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /web
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /web/.next/standalone ./
COPY --from=build --chown=node:node /web/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
