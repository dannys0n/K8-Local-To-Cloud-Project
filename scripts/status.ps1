$ErrorActionPreference = "Stop"
kubectl get nodes -o wide
kubectl get pods,svc,pvc,pdb -n tcp-lab -o wide
