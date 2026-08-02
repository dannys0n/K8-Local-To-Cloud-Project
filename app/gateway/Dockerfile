FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/tcp-gateway ./main.go

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/tcp-gateway /tcp-gateway
EXPOSE 9000 8404
ENTRYPOINT ["/tcp-gateway"]
