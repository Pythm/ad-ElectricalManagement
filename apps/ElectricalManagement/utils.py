def cancel_timer_handler(ADapi, handler, name) -> None:
    if handler is not None:
        if ADapi.timer_running(handler):
            try:
                ADapi.cancel_timer(handler)
            except Exception as e:
                ADapi.log(
                    f"Not able to stop timer handler for {name}. Exception: {e}",
                    level = 'DEBUG'
                )

def cancel_listen_handler(ADapi, handler, name) -> None:
    if handler is not None:
        try:
            ADapi.cancel_listen_state(handler)
        except Exception as e:
            ADapi.log(
                f"Not able to stop listen handler for {name}. Exception: {e}",
                level = 'DEBUG'
            )
