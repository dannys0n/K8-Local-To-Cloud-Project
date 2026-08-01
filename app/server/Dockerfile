FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/tcp-server ./main.go

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/tcp-server /tcp-server
EXPOSE 7000
ENTRYPOINT ["/tcp-server"]
