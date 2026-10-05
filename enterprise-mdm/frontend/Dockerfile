# syntax=docker/dockerfile:1
FROM node:20-slim AS base
WORKDIR /app
ENV NODE_ENV=development

FROM base AS deps
COPY package.json ./
RUN npm install

FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM deps AS build
COPY . .
ENV NODE_ENV=production
# Phase 15: Next.js bakes NEXT_PUBLIC_* variables into the client bundle
# at `next build` time, from the process environment `npm run build`
# actually runs under — a bare `docker build --build-arg
# NEXT_PUBLIC_API_BASE_URL=...` had no effect without this ARG/ENV pair
# (an undeclared build-arg is silently ignored, not an error), so the
# resulting bundle always pointed at api/lib/client.ts's hardcoded
# http://localhost:8000 fallback regardless of what was passed at build
# time. Declared here, not for the `dev` stage above (which never runs a
# build — `next dev` reads real runtime env directly).
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:20-slim AS production
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "run", "start"]
