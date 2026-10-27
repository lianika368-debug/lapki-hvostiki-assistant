"""Product scenarios with deterministic extracted facts; no external API calls."""
from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest
from assistant import Assistant, BookingState, SALON, build_application
from conversation import reconcile_service, render_facts
from models import Draft, Extraction, Conflict, Application, TZ, normalize_phone
from notifications import message_chunks


def make(extracted=None):
    ex, store, notifier = Mock(), Mock(), Mock()
    ex.extract.return_value = extracted or Extraction(intent='общее')
    notifier.enabled = True
    return Assistant(ex,store,notifier=notifier)


def turn(bot,message,state=None,history=None,revision=None):
    state = state or BookingState()
    state.last_request = 0
    return bot.respond(message,history or [],state,revision=revision)


def prepared(bot, **patch):
    state=BookingState(active=True,draft=Draft(phone='+79991234567',service='cat_full',**patch))
    bot._prepare(state)
    return state


def test_known_request_only_needs_contact():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(pet_type='собака',pet_breed='корги',pet_weight='12 кг',request_text='Нужен комплекс',schedule_note='в субботу после обеда')))
    h,s=turn(bot,'Корги, 12 кг, нужен комплекс в субботу после обеда')
    assert s.awaiting_field=='phone' and 'Чтобы администратор' in h[-1]['content']
    assert s.draft.pet_weight=='12 кг' and s.draft.service is None
    bot.extractor.extract.return_value=Extraction(intent='запись',action='continue',patch=Draft(phone='+79991234567'))
    h,s=turn(bot,'+79991234567',s,h)
    assert s.pending and not s.pending.owner_name and not s.pending.pet_age
    assert not s.pending.preferred_groomer and not s.pending.comment
    assert '12 кг' in h[-1]['content'] and 'для уточнения' in h[-1]['content']
    bot.store.save.assert_not_called()


def test_consult_recommendation_does_not_start_booking():
    bot=make(Extraction(intent='общее',recommendation='shedding',patch=Draft(pet_type='собака',request_text='Собака сильно линяет')))
    h,s=turn(bot,'Собака сильно линяет, не понимаю, нужна ли ей стрижка')
    assert 'экспресс-линьку' in h[-1]['content'] and '1 500' in h[-1]['content']
    assert not s.active and s.draft.service is None and not s.draft.phone
    assert 'телефон' not in h[-1]['content'].casefold()


def test_decline_during_booking_answers_price_without_contacts():
    bot=make(Extraction(intent='запись',action='book',price_services=['cat_full']))
    h,s=turn(bot,'Я не хочу записываться. Только узнать цену комплекса для кошки',BookingState(active=True))
    assert '2 500' in h[-1]['content'] and not s.active and not s.pending
    assert 'Как зовут' not in h[-1]['content'] and 'укажите телефон' not in h[-1]['content']


def test_price_and_duration_then_duration_followup():
    bot=make(Extraction(intent='цена',price_services=['cat_full']))
    h,s=turn(bot,'Сколько стоит комплекс для кошки и сколько длится?')
    assert '2 500' in h[-1]['content'] and 'Длительность' in h[-1]['content']
    bot.extractor.extract.return_value=Extraction(intent='общее',topics=['duration'])
    h,s=turn(bot,'А по времени?',s,h)
    assert 'Длительность' in h[-1]['content'] and '2 500' not in h[-1]['content']


def test_faq_inside_booking_preserves_request_without_next_question():
    bot=make(Extraction(intent='общее',topics=['preparation']))
    state=BookingState(active=True,draft=Draft(pet_weight='12 кг',request_text='Комплекс',pet_breed='корги'))
    h,s=turn(bot,'Как подготовиться?',state)
    assert h[-1]['content']==SALON['preparation']
    assert s.active and s.draft.pet_weight=='12 кг' and not s.pending


@pytest.mark.parametrize('phone',['123','+70000000000','+71111111111'])
def test_bad_phone_is_rejected_immediately(phone):
    bot=make(Extraction(intent='запись',action='continue',patch=Draft(phone=phone)))
    state=BookingState(active=True,awaiting_field='phone',draft=Draft(service='nails'))
    h,s=turn(bot,phone,state)
    assert 'не принято' in h[-1]['content'] and 'phone' in s.issues and not s.pending
    assert not s.draft.phone and 'мастер' not in h[-1]['content'].casefold()


@pytest.mark.parametrize('phone,expected',[('8 (999) 123-45-67','+79991234567'),('+44 7700 900123','+447700900123'),('+7 999 123 45 67','+79991234567')])
def test_real_format_phone_normalization(phone,expected):
    assert normalize_phone(phone)==expected


def test_invalid_time_edit_blocks_old_summary_and_preserves_other_edits():
    bot=make(Extraction(intent='запись',action='edit',patch=Draft(preferred_time='15:00',pet_name='Бублик')))
    s=prepared(bot,preferred_time='15:00'); old_revision=s.revision
    h,s=turn(bot,'Поменяйте время на 25:99, кличку на Бублик',s)
    assert s.draft.preferred_time=='15:00' and s.draft.pet_name=='Бублик'
    assert '25:99' in h[-1]['content'] and 'Применено: Питомец' in h[-1]['content']
    assert s.pending is None and s.revision is None
    turn(bot,'Подтверждаю',s,h,old_revision); bot.store.save.assert_not_called()
    h,s=turn(bot,'Оставить как было',s,h)
    assert s.pending.preferred_time.strftime('%H:%M')=='15:00'
    assert s.revision!=old_revision


def test_stale_revision_cannot_send_new_summary():
    bot=make(Extraction(intent='запись',action='edit',patch=Draft(preferred_time='16:00')))
    s=prepared(bot,preferred_time='15:00'); old=s.revision
    h,s=turn(bot,'Поменяйте время на 16:00',s)
    assert s.revision!=old
    h,s=turn(bot,'Подтверждаю',s,h,old)
    assert 'устаревшее' in h[-1]['content'];bot.store.save.assert_not_called()
    turn(bot,'Подтверждаю',s,h,s.revision);bot.store.save.assert_called_once()


@pytest.mark.parametrize('message',['не подтверждаю','Я не подтверждаю заявку','Что значит «Подтверждаю»?','"Подтверждаю"','Подтверждаю, но поменяйте время'])
def test_only_explicit_exact_consent_can_send(message):
    bot=make();s=prepared(bot)
    turn(bot,message,s)
    bot.store.save.assert_not_called();bot.notifier.send.assert_not_called()


def test_no_contact_no_application_even_if_direct_confirmation():
    bot=make();s=BookingState(active=True,draft=Draft(service='nails'))
    turn(bot,'Подтверждаю',s)
    bot.store.save.assert_not_called()


def test_free_schedule_does_not_invent_precise_time():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(service='cat_full',phone='+79991234567',schedule_note='в субботу после обеда')))
    h,s=turn(bot,'Хочу записаться в субботу после обеда')
    assert s.pending and s.pending.preferred_time is None
    assert 'в субботу после обеда' in h[-1]['content'] and 'Согласовать' in s.pending.handoff


def test_clear_date_or_master_does_not_block_request():
    bot=make(Extraction(intent='запись',action='edit',clear_fields=['preferred_date','preferred_groomer']))
    s=prepared(bot,preferred_groomer='Марина')
    h,s=turn(bot,'Дату согласовать, мастера пока не знаю',s)
    assert s.pending and s.pending.preferred_groomer is None and s.pending.preferred_date is None


def test_conflict_requires_only_disputed_field():
    bot=make(Extraction(intent='запись',action='edit',conflicts=[Conflict(field='preferred_time',question='Вы имеете в виду 12:00 или 15:00?')]))
    s=prepared(bot)
    h,s=turn(bot,'В 12:00 или 15:00',s)
    assert '12:00 или 15:00' in h[-1]['content'] and not s.pending
    bot.extractor.extract.return_value=Extraction(intent='запись',action='continue',patch=Draft(preferred_time='15:00'))
    h,s=turn(bot,'В 15:00',s,h)
    assert s.pending and not s.issues


def test_new_pet_does_not_inherit_old_parameters():
    bot=make(Extraction(intent='общее',action='new_pet',patch=Draft(pet_type='кошка',pet_name='Мурка')))
    s=BookingState(draft=Draft(phone='+79991234567',pet_type='собака',pet_breed='корги',pet_weight='12 кг',pet_size='средний',service='dog_medium',comment='Боится',preferred_groomer='Анна'))
    h,s=turn(bot,'Теперь о другом питомце, кошка Мурка',s)
    assert s.draft.pet_name=='Мурка' and s.draft.pet_type=='кошка'
    assert all(getattr(s.draft,k) is None for k in ('pet_breed','pet_weight','pet_size','service','comment','preferred_groomer'))
    assert s.draft.phone=='+79991234567'


def test_weight_never_assigns_dog_category_or_price():
    draft=Draft(pet_type='собака',pet_breed='корги',pet_weight='12 кг',service='dog_small')
    assert reconcile_service(draft).service is None
    answer=render_facts(Extraction(intent='цена',price_services=['dog_small']),SALON,draft)
    assert all(price in answer for price in ('2 200','3 200','4 500'))
    assert 'Подскажите, пожалуйста, размер вашей собаки' in answer
    assert 'границы' not in answer.lower() and 'по породе' not in answer.lower()
    draft.pet_size='средний';draft.request_text='комплекс'
    assert reconcile_service(draft).service=='dog_medium'
    answer=render_facts(Extraction(intent='цена',price_services=['dog_small']),SALON,draft)
    assert '3 200' in answer and '2 200' not in answer


def test_handoff_contains_context_and_unresolved_questions():
    bot=make();s=BookingState(active=True,draft=Draft(phone='+79991234567',pet_weight='12 кг',request_text='Нужен комплекс'),explained=['duration','included','price'])
    bot._prepare(s)
    assert 'длительность' in s.pending.handoff and 'состав' in s.pending.handoff
    assert 'Уточнить услугу' in s.pending.handoff and s.pending.pet_weight=='12 кг'


@pytest.mark.parametrize('value',['25:99','09:59','20:01','12:30:01'])
def test_invalid_exact_times(value):
    with pytest.raises(ValueError):Application(phone='+79991234567',service='nails',preferred_time=value)


def test_date_timezone_and_year_boundary():
    today=datetime.now(TZ).date()
    Application(phone='+79991234567',service='nails',preferred_date=today)
    Application(phone='+79991234567',service='nails',preferred_date=f'{today.year+1}-01-01')
    with pytest.raises(ValueError):Application(phone='+79991234567',service='nails',preferred_date=today-timedelta(days=1))


def test_model_failure_during_edit_disables_old_confirmation():
    bot=make();s=prepared(bot,preferred_time='15:00')
    bot.extractor.extract.side_effect=RuntimeError('secret')
    h,s=turn(bot,'Поменяйте время',s)
    assert s.pending is None and s.last_error and s.draft.preferred_time=='15:00'
    turn(bot,'Подтверждаю',s,h);bot.store.save.assert_not_called()


def test_long_unicode_message_and_telegram_chunking():
    bot=make();h,s=turn(bot,'🐾'*4001)
    assert s.last_error;bot.extractor.extract.assert_not_called()
    text='🐾'*5000
    chunks=list(message_chunks(text))
    assert ''.join(chunks)==text
    assert all(len(x.encode('utf-16-le'))//2<=3500 for x in chunks)


def test_price_uses_unclassified_complex_from_previous_context():
    draft=Draft(pet_type='собака',pet_breed='корги',pet_weight='12 кг',request_text='Нужен комплекс')
    answer=render_facts(Extraction(intent='цена',topics=['duration']),SALON,draft)
    assert all(value in answer for value in ('2 200','3 200','4 500','Длительность'))
    assert 'Какую услугу' not in answer


def test_time_conflict_blocks_even_if_model_picked_one():
    bot=make(Extraction(intent='запись',action='edit',patch=Draft(preferred_time='12:00')))
    h,s=turn(bot,'В 12:00 или 15:00',prepared(bot))
    assert not s.pending and '12:00 или 15:00' in h[-1]['content']


def test_weekday_comes_from_salon_timezone_not_model_guess():
    from conversation import explicit_patch
    today=datetime.now(TZ).date()
    saturday=today+timedelta(days=(5-today.weekday())%7)
    assert explicit_patch('в субботу после обеда')['preferred_date']==saturday.isoformat()


def test_booking_without_task_asks_task_instead_of_making_up_request():
    bot=make(Extraction(intent='запись',action='book'))
    h,s=turn(bot,'Хочу оставить заявку')
    assert s.awaiting_field=='request_text' and not s.draft.request_text
    assert 'Что передать' in h[-1]['content']


def test_agree_date_does_not_discard_explicit_time():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(phone='+79991234567',service='cat_full',preferred_time='15:00')))
    h,s=turn(bot,'Хочу записаться, дату согласовать, время 15:00')
    assert s.pending and s.pending.preferred_time.strftime('%H:%M')=='15:00'
    assert s.pending.preferred_date is None


def test_duration_followup_drops_model_repeated_price_keys():
    bot=make(Extraction(intent='цена',topics=['duration'],price_services=['cat_full']))
    h,s=turn(bot,'А по времени?',BookingState(draft=Draft(service='cat_full')))
    assert 'Длительность' in h[-1]['content'] and '2 500' not in h[-1]['content']


def test_plain_task_and_schedule_is_booking_even_if_model_says_consult():
    bot=make(Extraction(intent='общее',action='consult',patch=Draft(pet_breed='Корги',pet_weight='12 кг',request_text='комплекс')))
    h,s=turn(bot,'Корги, 12 кг, нужен комплекс в субботу после обеда')
    assert s.active and s.awaiting_field=='phone' and s.draft.pet_type=='собака'


def test_model_cannot_infer_size_from_kilograms():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(pet_breed='Корги',pet_weight='12 кг',pet_size='мелкий',service='dog_small',request_text='Комплекс')))
    h,s=turn(bot,'Корги, 12 кг, нужен комплекс в субботу после обеда')
    assert s.draft.pet_size is None and s.draft.service is None


def test_explicit_size_is_used_for_service_and_price():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(pet_type='собака',request_text='Комплекс',phone='+79991234567')))
    h,s=turn(bot,'Хочу записаться на комплекс, собака среднего размера',None)
    assert s.pending.service=='dog_medium' and '3 200' in h[-1]['content']


def test_new_pet_words_clear_even_if_model_misses_action():
    bot=make(Extraction(intent='общее',patch=Draft(pet_type='кошка')))
    s=BookingState(draft=Draft(pet_name='Шарик',pet_weight='12 кг',comment='Боится фена'))
    h,s=turn(bot,'Теперь о кошке',s)
    assert not s.draft.pet_name and not s.draft.pet_weight and not s.draft.comment


def test_today_past_time_is_rejected_when_both_known(monkeypatch):
    import models
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2030,1,31,16,0,tzinfo=TZ)
    monkeypatch.setattr(models,'datetime',FixedClock)
    with pytest.raises(ValueError):
        Application(phone='+79991234567',service='nails',preferred_date='2030-01-31',preferred_time='10:00')
    Application(phone='+79991234567',service='nails',preferred_date='2030-02-01',preferred_time='10:00')


def test_recommendation_does_not_repeat_price_even_if_model_requests_it():
    bot=make(Extraction(intent='цена',recommendation='shedding',price_services=['shedding']))
    h,s=turn(bot,'Собака сильно линяет, нужна ли стрижка?')
    assert h[-1]['content'].count('1 500')==1
    assert 'справочник' not in h[-1]['content'].lower()
    assert 'подскажет мастер' in h[-1]['content'].lower()


def test_failed_model_on_pet_switch_cannot_leave_old_application_sendable():
    bot=make();s=prepared(bot)
    bot.extractor.extract.side_effect=RuntimeError('network')
    h,s=turn(bot,'Теперь о другом питомце',s)
    assert not s.pending and s.draft.service=='cat_full' and s.last_error
    turn(bot,'Подтверждаю',s,h);bot.store.save.assert_not_called()


def test_duration_question_does_not_add_unasked_composition():
    bot=make(Extraction(intent='цена',price_services=['cat_full'],topics=['duration','included']))
    h,s=turn(bot,'Сколько стоит комплекс для кошки и сколько длится?')
    assert '2 500' in h[-1]['content'] and 'Длительность' in h[-1]['content']
    assert 'профессиональной косметикой' not in h[-1]['content']


def test_price_duration_does_not_turn_into_model_complex_recommendation():
    bot=make(Extraction(intent='цена',price_services=['cat_full'],topics=['duration','included'],recommendation='complex'))
    h,s=turn(bot,'Сколько стоит комплекс для кошки и сколько длится?')
    assert '2 500' in h[-1]['content'] and 'Длительность' in h[-1]['content']
    assert 'профессиональной косметикой' not in h[-1]['content']


def test_booking_with_duration_question_answers_before_contact_question():
    bot=make(Extraction(intent='запись',action='book',patch=Draft(service='cat_full'),topics=['duration']))
    h,s=turn(bot,'Хочу записаться на комплекс для кошки, сколько длится?')
    assert 'Длительность' in h[-1]['content'] and 'Чтобы администратор' in h[-1]['content']
    assert 'duration' in s.explained


def test_handoff_does_not_claim_unspoken_recommendation():
    bot=make(Extraction(intent='запись',action='book',recommendation='complex',patch=Draft(phone='+79991234567',service='cat_full')))
    h,s=turn(bot,'Хочу оставить заявку на комплекс для кошки')
    assert s.pending and 'recommendation' not in s.explained
    assert 'предложены варианты' not in s.pending.handoff


def test_edit_after_handoff_does_not_claim_to_update_saved_request():
    bot=make(Extraction(intent='запись',action='edit',patch=Draft(preferred_time='16:00')))
    s=prepared(bot);s.saved=True;s.active=False
    original=s.draft.model_dump();number=s.application_id
    h,s=turn(bot,'Поменяйте время на 16:00',s)
    assert s.draft.model_dump()==original and s.application_id==number
    assert 'не изменялись' in h[-1]['content'] and number in h[-1]['content']
    bot.store.save.assert_not_called();bot.notifier.send.assert_not_called()
