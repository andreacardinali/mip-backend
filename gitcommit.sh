./db_dump.sh
./scripts/generate_env_example.sh .env .env.example --force
git add .
git commit
git push
