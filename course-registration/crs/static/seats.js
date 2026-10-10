// 名额展示仅用于提前提示；提交时仍由服务器在事务内检查。
async function refreshSeats() {
  const notice = document.getElementById('seat-notice');
  try {
    const response = await fetch('/student/api/seats', {headers: {'Accept': 'application/json'}});
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
    const seats = await response.json();
    const full = [];
    document.querySelectorAll('[data-seat]').forEach(cell => {
      const state = seats[cell.dataset.seat];
      if (state) cell.textContent = `${state.enrolled}/10${state.full ? ' · 已满' : ''}`;
    });
    document.querySelectorAll('select[name="primary"]').forEach(select => {
      if (seats[select.value]?.full) full.push(select.selectedOptions[0].textContent);
    });
    notice.textContent = full.length ? `以下首选课已满（含本人已占名额的情况）：${full.join('、')}。已有注册保留，新注册请改选。` : '名额已刷新；提交时服务器会再次校验。';
    notice.className = `alert ${full.length ? 'warning' : 'info'}`;
  } catch (_) {
    notice.textContent = '名额刷新失败，请检查网络或重新登录。当前显示可能过期；提交仍须通过服务器校验。';
    notice.className = 'alert warning';
  }
}
setInterval(refreshSeats, 5000);
refreshSeats();
