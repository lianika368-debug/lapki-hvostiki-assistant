"""Review keeps dates unambiguous and treats visitor values only as text."""
from datetime import date, datetime, time

from assistant import SALON
from models import Application, TZ
from ui_summary import desired_when, render_summary


def example(**patch):
    # Rendering tests are independent from the current appointment validation date.
    return Application.model_construct(phone='+79990000000', service='nails', **patch)


def test_date_and_time_are_combined_in_russian():
    day = date(datetime.now(TZ).year, 10, 30)
    app = example(preferred_date=day, preferred_time=time(16, 30))
    assert desired_when(app) == '30 октября, 16:30'
    html = render_summary(app, SALON)
    assert '30 октября, 16:30' in html and day.isoformat() not in html
    assert 'Запись не подтверждена.' in html


def test_other_year_is_not_hidden():
    year = datetime.now(TZ).year + 1
    assert desired_when(example(preferred_date=date(year, 1, 5))) == f'5 января {year} · время согласовать'


def test_missing_date_and_time_are_not_invented():
    assert desired_when(example()) == 'Дату и время согласовать'
    assert desired_when(example(preferred_time=time(12))) == '12:00 · дату согласовать'


def test_all_optional_values_and_handoff_remain_visible_and_escaped():
    app = example(owner_name='<b>Тест</b>', pet_name='Плюш', pet_type='собака',
                  pet_breed='корги', pet_age='3 года', pet_weight='12 кг',
                  pet_size='средний', preferred_groomer='Анна',
                  request_text='Стрижка когтей', schedule_note='После обеда',
                  comment='<script>alert(1)</script>',
                  handoff='Обсуждалось: объяснена подготовка\nОриентировочная стоимость: 500 ₽\nОсталось: Согласовать точные дату и время')
    html = render_summary(app, SALON)
    for value in ('Плюш', 'собака', 'корги', '3 года', '12 кг', 'средний', 'Анна',
                  'После обеда', 'объяснена подготовка', 'Согласовать точные дату и время'):
        assert value in html
    assert '<script>' not in html and '<b>Тест</b>' not in html
    assert '&lt;script&gt;' in html and '&lt;b&gt;Тест&lt;/b&gt;' in html
    assert html.count('500 ₽') == 1


def test_unselected_service_remains_for_administrator():
    app = Application.model_construct(phone='+79990000000', request_text='Помочь выбрать уход')
    html = render_summary(app, SALON)
    assert 'Помочь выбрать уход' in html and 'Уточнит администратор' in html
    assert '₽' not in html
