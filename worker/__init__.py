"""포트 없는 작업 워커 (docs/16 ADR-005). server 를 import 하지 않고 core/ 만 쓴다."""
REQUIRED_SCHEMA = "0004"   # 이 워커가 필요로 하는 최소 마이그레이션 버전(D4: 적용은 서버만)
GPU_SLOT = "gpu0"          # GPU 슬롯 1
