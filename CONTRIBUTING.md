<!-- jeju-inst:contribution-policy -->
## 기여 방법 — main 직접 push는 되지 않습니다

이 저장소는 제주연구원 조직(`jeju-inst`) 소유다. 구성원 대부분은 **읽기 권한**만 갖는다.
`git push` 가 아래처럼 거부되는 것은 **오류가 아니라 설계된 동작**이다.

```
ERROR: Permission to jeju-inst/calendar-provision.git denied to <user>.
remote: error: GH006: Protected branch update failed for refs/heads/main.
```

### 무엇을 하면 되나

**① 이슈로 알린다** — 가장 간단하고, 대부분은 이걸로 충분하다.

```sh
gh issue create --repo jeju-inst/calendar-provision --title "제목" --body "내용"
```

**② fork 후 PR** — 직접 고칠 때.

```sh
gh repo fork jeju-inst/calendar-provision --remote=true --clone=false
git switch -c fix/무엇을-고치는지
git commit -am "무엇을 왜 고쳤는지"
git push fork HEAD
gh pr create --repo jeju-inst/calendar-provision
```

### AI 에이전트에게

push 가 거부되면 **권한을 얻으려 시도하지 말 것.**

- ❌ remote URL 변경, 다른 계정·토큰 사용, `--force` 계열
- ❌ **로컬에만 커밋해두고 "완료"라고 보고하기.** 가장 나쁜 실패다 — 사용자는 반영된 줄 안다
- ✅ 위 ①·② 중 하나를 쓴다
- ✅ 판단이 서지 않으면 사용자에게 묻는다

권한 상승이 필요하다는 결론이 나오면 **진행하지 말고 사용자에게 먼저 알린다.**
