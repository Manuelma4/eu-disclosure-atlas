# syntax=docker/dockerfile:1
FROM golang:1.27 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
ARG VERSION=dev
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/realtime-gateway ./cmd/realtime-gateway

FROM gcr.io/distroless/static-debian13:nonroot
COPY --from=build /out/realtime-gateway /realtime-gateway
USER nonroot:nonroot
EXPOSE 8090
ENTRYPOINT ["/realtime-gateway"]
