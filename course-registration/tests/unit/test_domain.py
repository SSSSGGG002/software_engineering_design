import pytest
from crs.domain import TimeSlot, find_conflicts, gpa
from crs.services.registration import parse_id_list
from crs.errors import ValidationError

@pytest.mark.parametrize('days,start,end',[('X','08:00','09:00'),('M','25:00','26:00'),('M','10:00','09:00'),('','08:00','09:00')])
def test_invalid_slots(days,start,end):
    with pytest.raises(ValueError): TimeSlot.parse(days,start,end)

def test_half_open_intervals():
    a=TimeSlot.parse('MW','08:00','10:00')
    assert not a.overlaps(TimeSlot.parse('M','10:00','11:00'))
    assert not a.overlaps(TimeSlot.parse('T','09:00','11:00'))
    assert a.overlaps(TimeSlot.parse('W','09:00','11:00'))
    assert find_conflicts({1:a,2:a})==[(1,2)]
    assert str(a)=='周一、周三 08:00-10:00'

@pytest.mark.parametrize('grades,expected',[([],None),(['I'],None),(['A','F','I'],2.0),(['B','C'],2.5)])
def test_gpa(grades,expected): assert gpa(grades)==expected

def test_id_parser():
    assert parse_id_list(['','1',' 2 '])==[1,2]
    with pytest.raises(ValidationError): parse_id_list(['x'])
