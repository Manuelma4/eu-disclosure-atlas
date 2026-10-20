# syntax=docker/dockerfile:1
FROM golang:1.27 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
ARG VERSION=dev
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/ ./cmd/alerts ./cmd/webhook-sink

FROM gcr.io/distroless/static-debian13:nonroot
COPY --from=build /out/alerts /out/webhook-sink /
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/alerts"]
