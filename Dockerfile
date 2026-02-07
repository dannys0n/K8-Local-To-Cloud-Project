# Minimal smoke-test image
FROM alpine:3.19

# Run something trivial so the container does real work
CMD ["echo", "CI Docker build succeeded"]
