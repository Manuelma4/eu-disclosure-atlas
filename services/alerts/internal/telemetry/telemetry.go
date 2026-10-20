// Package telemetry configures structured logs (slog, JSON) and OpenTelemetry traces and metrics.
package telemetry

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.37.0"
)

// Logger returns a JSON logger at ATLAS_LOG_LEVEL (debug, info, warn, error; default info).
func Logger(service string) *slog.Logger {
	var level slog.Level
	_ = level.UnmarshalText([]byte(strings.ToUpper(os.Getenv("ATLAS_LOG_LEVEL"))))
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})).With("service", service)
}

// Setup installs global tracer and meter providers exporting OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set
// (OTEL_EXPORTER_OTLP_PROTOCOL: grpc, or http/protobuf by default as in the OpenTelemetry specification);
// otherwise telemetry stays a no-op. HTTP server spans and http.server.request.duration come from otelhttp.
// The returned function flushes on shutdown.
func Setup(ctx context.Context, service, version string) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		return func(context.Context) error { return nil }, nil
	}
	res, err := resource.Merge(resource.Default(), resource.NewWithAttributes(semconv.SchemaURL,
		semconv.ServiceName(service), semconv.ServiceVersion(version)))
	if err != nil {
		return nil, err
	}
	var spans sdktrace.SpanExporter
	var metrics sdkmetric.Exporter
	if os.Getenv("OTEL_EXPORTER_OTLP_PROTOCOL") == "grpc" {
		if spans, err = otlptracegrpc.New(ctx); err == nil {
			metrics, err = otlpmetricgrpc.New(ctx)
		}
	} else {
		if spans, err = otlptracehttp.New(ctx); err == nil {
			metrics, err = otlpmetrichttp.New(ctx)
		}
	}
	if err != nil {
		return nil, err
	}
	tracer := sdktrace.NewTracerProvider(sdktrace.WithBatcher(spans), sdktrace.WithResource(res))
	meter := sdkmetric.NewMeterProvider(sdkmetric.WithResource(res),
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metrics, sdkmetric.WithInterval(15*time.Second))))
	otel.SetTracerProvider(tracer)
	otel.SetMeterProvider(meter)
	return func(ctx context.Context) error { return errors.Join(tracer.Shutdown(ctx), meter.Shutdown(ctx)) }, nil
}
